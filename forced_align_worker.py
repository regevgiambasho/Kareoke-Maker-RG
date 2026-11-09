from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np


def emit(frac: float, message: str) -> None:
    print(f"KPROGRESS|{max(0.0, min(1.0, frac)):.4f}|{message}", flush=True)


def detect_language(text: str, requested: str) -> str:
    if requested and requested != "auto":
        return requested
    if re.search(r"[\u0590-\u05FF]", text):
        return "he"
    if re.search(r"[\u0600-\u06FF]", text):
        return "ar"
    return "en"


def lyric_lines(text: str) -> list[str]:
    return [
        re.sub(r"\s+", " ", line).strip()
        for line in text.splitlines()
        if line.strip()
    ]


def word_count(text: str) -> int:
    return max(1, len([x for x in re.split(r"\s+", text.strip()) if x]))


# Compatibility helpers retained for older tests.
def group_lyrics(lines: list[str], target_words: int = 38, max_lines: int = 7) -> list[dict]:
    chunks = []
    current = []
    words = 0
    for line in lines:
        wc = word_count(line)
        if current and (words + wc > target_words or len(current) >= max_lines):
            text = " ".join(current)
            chunks.append({"text": text, "lines": list(current), "words": word_count(text)})
            current, words = [], 0
        current.append(line)
        words += wc
    if current:
        text = " ".join(current)
        chunks.append({"text": text, "lines": list(current), "words": word_count(text)})
    return chunks


def frame_activity(audio: np.ndarray, sr: int = 16000):
    frame = max(1, int(sr * 0.40))
    hop = max(1, int(sr * 0.20))
    times, rms = [], []

    for start in range(0, max(1, len(audio) - frame + 1), hop):
        end = min(len(audio), start + frame)
        chunk = audio[start:end]
        if chunk.size == 0:
            continue
        times.append((start + end) / (2.0 * sr))
        rms.append(
            float(np.sqrt(np.mean(np.square(chunk), dtype=np.float64) + 1e-12))
        )

    if not rms:
        duration = max(0.1, len(audio) / sr)
        return np.array([0.0, duration]), np.array([0.0, 1.0])

    rms = np.asarray(rms)
    times = np.asarray(times)
    peak = float(np.percentile(rms, 95))
    floor = float(np.percentile(rms, 20))
    threshold = max(1e-5, floor * 1.7, peak * 0.03)
    active = (rms >= threshold).astype(np.float64)

    if active.sum() < 2:
        active[:] = 1.0

    cumulative = np.cumsum(active)
    cumulative /= cumulative[-1]
    return times, cumulative


def time_at_fraction(times, cumulative, fraction, duration):
    fraction = min(1.0, max(0.0, float(fraction)))
    idx = int(np.searchsorted(cumulative, fraction, side="left"))
    idx = min(max(idx, 0), len(times) - 1)
    return float(min(duration, max(0.0, times[idx])))


def build_line_windows(lines: list[str], audio: np.ndarray, sr: int = 16000):
    duration = max(0.1, len(audio) / float(sr))
    times, cumulative = frame_activity(audio, sr)
    weights = [word_count(line) for line in lines]
    total = max(1, sum(weights))
    cursor = 0
    windows = []

    for i, (line, weight) in enumerate(zip(lines, weights)):
        f1 = cursor / total
        cursor += weight
        f2 = cursor / total

        rough_start = time_at_fraction(times, cumulative, f1, duration)
        rough_end = time_at_fraction(times, cumulative, f2, duration)

        start = max(0.0, rough_start - 2.8)
        end = min(duration, rough_end + 3.2)

        if end - start < 4.5:
            center = (start + end) / 2.0
            start = max(0.0, center - 2.25)
            end = min(duration, start + 4.5)
            start = max(0.0, end - 4.5)

        windows.append({
            "index": i,
            "text": line,
            "start": start,
            "end": end,
            "rough_start": rough_start,
            "rough_end": rough_end,
        })

    return windows


def assign_chunk_windows(chunks: list[dict], audio: np.ndarray, sr: int = 16000, pad_seconds: float = 5.0):
    # Compatibility wrapper for old unit tests.
    lines = [c.get("text", "") for c in chunks]
    windows = build_line_windows(lines, audio, sr)
    return [{**chunk, **window} for chunk, window in zip(chunks, windows)]


def extract_words(aligned: dict, offset: float = 0.0) -> list[dict]:
    words = []
    for seg in aligned.get("segments", []) or []:
        for w in seg.get("words", []) or []:
            token = str(w.get("word") or "").strip()
            start = w.get("start")
            end = w.get("end")
            if not token or start is None or end is None:
                continue
            item = {
                "text": token,
                "start": float(start) + offset,
                "end": float(end) + offset,
            }
            if w.get("score") is not None:
                item["score"] = float(w["score"])
            words.append(item)
    return words


def fallback_words(text: str, start: float, end: float) -> list[dict]:
    tokens = [x for x in text.split() if x]
    if not tokens:
        return []

    duration = max(0.2, end - start)
    weights = [max(1, len(t)) for t in tokens]
    total = float(sum(weights))
    cursor = start
    result = []

    for i, token in enumerate(tokens):
        span = duration * weights[i] / total
        token_end = end if i == len(tokens) - 1 else min(end, cursor + span)
        result.append({
            "text": token,
            "start": cursor,
            "end": max(cursor + 0.03, token_end),
            "score": 0.0,
            "interpolated": True,
        })
        cursor = token_end

    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True)
    parser.add_argument("--lyrics", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--language", default="auto")
    args = parser.parse_args()

    lyrics = Path(args.lyrics).read_text(encoding="utf-8").strip()
    lines = lyric_lines(lyrics)
    if not lines:
        print("KERROR|Lyrics text is empty", flush=True)
        return 1

    language = detect_language(lyrics, args.language)

    try:
        import torch
        import whisperx

        device = "cuda" if torch.cuda.is_available() else "cpu"

        emit(0.05, f"Loading {language} forced-alignment model")
        model_a, metadata = whisperx.load_align_model(
            language_code=language,
            device=device,
        )

        emit(0.15, "Loading isolated vocals")
        audio = np.asarray(
            whisperx.load_audio(args.audio),
            dtype=np.float32,
        ).reshape(-1)

        duration = max(0.1, len(audio) / 16000.0)
        windows = build_line_windows(lines, audio)

        emit(0.22, f"Precision Sync — {len(lines)} lyric lines")

        segments, all_words = [], []
        failed = 0
        previous_end = 0.0

        for i, window in enumerate(windows):
            start = max(0.0, float(window["start"]), previous_end - 0.75)
            end = min(duration, float(window["end"]))
            if end <= start + 0.5:
                end = min(duration, start + 4.5)

            local_audio = audio[int(start * 16000):min(len(audio), int(end * 16000))]
            local_duration = max(0.1, len(local_audio) / 16000.0)

            emit(
                0.25 + 0.64 * (i / max(1, len(lines))),
                f"Aligning lyric line {i + 1}/{len(lines)}",
            )

            try:
                aligned = whisperx.align(
                    [{
                        "start": 0.0,
                        "end": local_duration,
                        "text": window["text"],
                    }],
                    model_a,
                    metadata,
                    local_audio,
                    device,
                    return_char_alignments=False,
                )
                words = extract_words(aligned, offset=start)
                if not words:
                    raise RuntimeError("No timed words returned")
                seg_start = min(w["start"] for w in words)
                seg_end = max(w["end"] for w in words)
            except Exception as exc:
                failed += 1
                print(
                    f"KWARN|Line {i + 1} alignment fallback: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )
                seg_start = max(previous_end, float(window["rough_start"]))
                seg_end = min(
                    duration,
                    max(seg_start + 0.5, float(window["rough_end"])),
                )
                words = fallback_words(window["text"], seg_start, seg_end)

            segments.append({
                "start": float(seg_start),
                "end": float(seg_end),
                "text": window["text"],
                "words": [
                    {
                        "word": w["text"],
                        "start": w["start"],
                        "end": w["end"],
                        "score": w.get("score"),
                    }
                    for w in words
                ],
                "alignment_fallback": any(w.get("interpolated") for w in words),
            })
            all_words.extend(words)
            previous_end = max(previous_end, seg_end)

        scores = [
            float(w["score"])
            for w in all_words
            if w.get("score") is not None and not w.get("interpolated")
        ]

        payload = {
            "language": language,
            "alignment_mode": "line-by-line-exact-text-forced-alignment",
            "device": device,
            "engine": "whisperx-ctc-align",
            "words": all_words,
            "segments": segments,
            "alignment_score": sum(scores) / len(scores) if scores else None,
            "line_count": len(lines),
            "failed_line_count": failed,
        }

        Path(args.output).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        emit(
            1.0,
            "Precision Sync complete"
            + (f" · {failed} fallback line(s)" if failed else ""),
        )
        return 0

    except Exception as exc:
        print(f"KERROR|{type(exc).__name__}: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
