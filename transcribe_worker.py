from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def emit(frac: float, message: str) -> None:
    print(f"KPROGRESS|{max(0.0, min(1.0, frac)):.4f}|{message}", flush=True)


def fallback_words(segments: list[dict]) -> list[dict]:
    words: list[dict] = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        tokens = text.split()
        if not tokens:
            continue
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", start + 0.5))
        duration = max(0.15, end - start)
        weights = [max(1, len(t.strip())) for t in tokens]
        total = float(sum(weights))
        cursor = start
        for idx, token in enumerate(tokens):
            part = duration * weights[idx] / total
            w_end = end if idx == len(tokens) - 1 else min(end, cursor + part)
            words.append({"text": token, "start": cursor, "end": w_end})
            cursor = w_end
    return words


def aligned_words(aligned: dict) -> list[dict]:
    words: list[dict] = []
    for seg in aligned.get("segments", []):
        for w in seg.get("words", []) or []:
            token = (w.get("word") or "").strip()
            if not token:
                continue
            start = w.get("start")
            end = w.get("end")
            if start is None or end is None:
                continue
            words.append({"text": token, "start": float(start), "end": float(end)})
    return words


def run_faster_whisper(args) -> dict:
    try:
        import torch
        from faster_whisper import WhisperModel, BatchedInferencePipeline
    except Exception as exc:
        raise RuntimeError(f"Unable to import faster-whisper: {exc}") from exc

    device = "cuda" if torch.cuda.is_available() else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"

    emit(0.10, f"Loading fast {args.model} timing model on {device}")

    model_kwargs = {
        "device": device,
        "compute_type": compute_type,
    }
    if device == "cpu":
        model_kwargs["cpu_threads"] = max(1, int(args.cpu_threads))

    model = WhisperModel(args.model, **model_kwargs)
    batched = BatchedInferencePipeline(model=model)

    emit(0.22, "Running fast timing transcription")
    segments_gen, info = batched.transcribe(
        args.audio,
        batch_size=max(1, int(args.batch_size)),
        language=None if args.language == "auto" else args.language,
        word_timestamps=True,
        vad_filter=(args.vad == "yes"),
        condition_on_previous_text=False,
    )

    duration = max(float(getattr(info, "duration", 0.0) or 0.0), 0.1)
    detected = getattr(info, "language", None) or (
        args.language if args.language != "auto" else "unknown"
    )

    words: list[dict] = []
    segments: list[dict] = []

    for seg in segments_gen:
        seg_words: list[dict] = []
        for word in getattr(seg, "words", None) or []:
            token = str(getattr(word, "word", "") or "").strip()
            start = getattr(word, "start", None)
            end = getattr(word, "end", None)
            if not token or start is None or end is None:
                continue
            item = {
                "text": token,
                "start": float(start),
                "end": float(end),
            }
            words.append(item)
            seg_words.append({
                "word": token,
                "start": float(start),
                "end": float(end),
            })

        segments.append({
            "start": float(seg.start),
            "end": float(seg.end),
            "text": str(seg.text or "").strip(),
            "words": seg_words,
        })

        local = min(1.0, float(seg.end) / duration)
        emit(0.24 + local * 0.70, f"Fast timing pass — {int(local * 100)}%")

    if not words:
        words = fallback_words(segments)

    return {
        "language": detected,
        "alignment_mode": "faster-whisper-word-timestamps",
        "device": device,
        "model": args.model,
        "engine": "faster-whisper",
        "words": words,
        "segments": segments,
    }


def run_whisperx(args) -> dict:
    try:
        import torch
        import whisperx
    except Exception as exc:
        raise RuntimeError(f"Unable to import WhisperX: {exc}") from exc

    device = "cuda" if torch.cuda.is_available() else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"
    batch_size = max(1, int(args.batch_size))

    emit(0.12, f"Loading {args.model} model on {device}")

    model = whisperx.load_model(
        args.model,
        device,
        compute_type=compute_type,
        language=None if args.language == "auto" else args.language,
    )
    emit(0.28, "Loading vocal audio")
    audio = whisperx.load_audio(args.audio)

    emit(0.36, "Transcribing vocals")
    result = model.transcribe(audio, batch_size=batch_size)
    detected = result.get("language") or (
        args.language if args.language != "auto" else "unknown"
    )

    words = []
    alignment_mode = "segment-fallback"

    if args.align == "yes" and detected not in ("unknown", None, ""):
        try:
            emit(0.72, f"Aligning words ({detected})")
            align_model, metadata = whisperx.load_align_model(
                language_code=detected, device=device
            )
            aligned = whisperx.align(
                result["segments"],
                align_model,
                metadata,
                audio,
                device,
                return_char_alignments=False,
            )
            words = aligned_words(aligned)
            if words:
                alignment_mode = "forced-alignment"
        except Exception as exc:
            print(f"KWARN|Alignment fallback: {exc}", flush=True)

    if not words:
        emit(0.84, "Building fallback word timings")
        words = fallback_words(result.get("segments", []))

    return {
        "language": detected,
        "alignment_mode": alignment_mode,
        "device": device,
        "model": args.model,
        "engine": "whisperx",
        "words": words,
        "segments": result.get("segments", []),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default="small")
    parser.add_argument("--language", default="auto")
    parser.add_argument("--align", choices=["yes", "no"], default="yes")
    parser.add_argument(
        "--engine",
        choices=["whisperx", "faster-whisper"],
        default="whisperx",
    )
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--cpu-threads", type=int, default=2)
    parser.add_argument("--vad", choices=["yes", "no"], default="yes")
    args = parser.parse_args()

    emit(0.03, "Starting transcription worker")

    try:
        if args.engine == "faster-whisper":
            payload = run_faster_whisper(args)
        else:
            payload = run_whisperx(args)

        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        emit(1.0, "Transcription complete")
        return 0

    except Exception as exc:
        print(f"KERROR|{type(exc).__name__}: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
