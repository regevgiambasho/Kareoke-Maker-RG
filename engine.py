from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable

from project_store import CACHE_ROOT, PROJECTS_ROOT, get_project, upsert_project
from lyrics_engine import (
    RTL_LANGUAGES,
    align_verified_lyrics,
    canonical_lines,
    fetch_licensed_lyrics,
    fetch_licensed_lyrics_with_status,
    fetch_lyricsovh_title_only,
    fetch_lrclib_lyrics,
    parse_musixmatch_url_hint,
    parse_synced_lyrics_lines,
    synced_lyrics_compatible,
    short_transcript_hint,
)


class KaraokeError(RuntimeError):
    pass


ProgressCallback = Callable[[float, str, str], None]


PERFORMANCE_PRESETS = {
    "Fast": {
        "whisper_model": "base",
        "timing_model": "base",
        "transcribe_engine": "faster-whisper",
        "align": False,
        "asr_batch_size": 8,
        "vad_filter": True,
        "separation_model": "UVR_MDXNET_KARA_2.onnx",
        "segment_size": 256,
        "batch_size": 4,
        "overlap": 0.05,
        "chunk_duration": 300,
        "threads": "auto",
        "render_preset": "medium",
        "render_crf": 14,
    },
    "Balanced": {
        "whisper_model": "small",
        "timing_model": "base",
        "transcribe_engine": "whisperx",
        "align": True,
        "asr_batch_size": 4,
        "vad_filter": True,
        "separation_model": "UVR-MDX-NET-Inst_HQ_3.onnx",
        "segment_size": 128,
        "batch_size": 2,
        "overlap": 0.10,
        "chunk_duration": 300,
        "threads": "auto",
        "render_preset": "medium",
        "render_crf": 14,
    },
    "Best quality": {
        "whisper_model": "medium",
        "timing_model": "small",
        "transcribe_engine": "whisperx",
        "align": True,
        "asr_batch_size": 2,
        "vad_filter": True,
        "separation_model": "UVR-MDX-NET-Inst_HQ_3.onnx",
        "segment_size": 256,
        "batch_size": 1,
        "overlap": 0.25,
        "chunk_duration": 120,
        "threads": "auto",
        "render_preset": "medium",
        "render_crf": 14,
    },
}

STYLE_PRESETS = {
    "Classic": {
        "font": "DejaVu Sans",
        "font_size": 48,
        "primary": "FFFFFF",
        "highlight": "00D7FF",
        "outline": "000000",
        "outline_width": 3,
        "shadow": 1,
        "background": "101018",
    },
    "Neon": {
        "font": "DejaVu Sans",
        "font_size": 50,
        "primary": "F4F0FF",
        "highlight": "FF4FD8",
        "outline": "24003A",
        "outline_width": 4,
        "shadow": 2,
        "background": "080611",
    },
    "Minimal": {
        "font": "DejaVu Sans",
        "font_size": 44,
        "primary": "F5F5F5",
        "highlight": "6EE7B7",
        "outline": "111111",
        "outline_width": 2,
        "shadow": 0,
        "background": "111111",
    },
    "Kids": {
        "font": "DejaVu Sans",
        "font_size": 54,
        "primary": "FFFFFF",
        "highlight": "FFD84D",
        "outline": "5B2A86",
        "outline_width": 4,
        "shadow": 2,
        "background": "25113D",
    },
}

SEPARATION_MODEL = "UVR-MDX-NET-Inst_HQ_3.onnx"  # legacy fallback


@dataclass
class BuildConfig:
    performance_mode: str = "Fast"
    language: str = "auto"
    use_original_video: bool = True
    style_preset: str = "Classic"
    font_size: int | None = None
    whisper_model_override: str | None = None
    use_verified_lyrics: bool = True
    require_verified_lyrics: bool = True
    manual_lyrics_text: str | None = None
    use_song_recognition: bool = False
    song_title: str | None = None
    artist_name: str | None = None
    musixmatch_url_hint: str | None = None
    custom_output_filename: str | None = None
    rotation_override: int | None = None

    def resolved(self) -> dict:
        perf = dict(PERFORMANCE_PRESETS[self.performance_mode])
        if self.whisper_model_override:
            perf["whisper_model"] = self.whisper_model_override
        if perf.get("threads") == "auto":
            perf["threads"] = max(2, min(8, os.cpu_count() or 2))
        style = dict(STYLE_PRESETS[self.style_preset])
        if self.font_size:
            style["font_size"] = int(self.font_size)
        return {"performance": perf, "style": style}


@dataclass
class BuildResult:
    project_id: str
    project_dir: Path
    video_path: Path
    instrumental_path: Path
    subtitles_path: Path
    transcript_path: Path
    lines_path: Path
    detected_language: str
    resumed_stages: list[str]
    lyrics_verified: bool = False
    lyrics_source: str = "whisper"
    lyrics_confidence: float = 0.0


def _emit(cb: ProgressCallback | None, pct: float, message: str, stage: str) -> None:
    if cb:
        cb(max(0.0, min(1.0, pct)), message, stage)


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._\-א-ת]+", "_", name.strip())
    cleaned = cleaned.strip("._")
    return cleaned or "karaoke"




def sanitize_output_filename(name: str) -> str:
    """
    Sanitize a user-provided output filename and always return an .mp4 filename.
    The user may type either a bare filename or one ending in .mp4.
    """
    raw = (name or "").strip()
    if raw.lower().endswith(".mp4"):
        raw = raw[:-4]

    # Keep Hebrew/Unicode letters, numbers, spaces, dashes and common safe punctuation.
    raw = re.sub(r'[<>:"/\\|?*\x00-\x1F]+', " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip(" .-_")

    if not raw:
        return "karaoke.mp4"

    return f"{raw}.mp4"


def build_output_filename(
    source_path: Path,
    song_title: str | None,
    artist_name: str | None,
) -> str:
    """
    Final filename template:
      Song Title - Performing Artist - קריוקי.mp4

    Falls back to the uploaded filename stem when metadata is missing.
    """
    source_stem = _safe_name(source_path.stem)
    title = _safe_name(song_title) if song_title else source_stem
    artist = _safe_name(artist_name) if artist_name else "Unknown Artist"
    return f"{title} - {artist} - קריוקי.mp4"

def file_sha256(path: Path, block_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(block_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def bytes_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ensure_tools() -> None:
    missing = []
    for tool in ("ffmpeg", "ffprobe", "audio-separator"):
        if shutil.which(tool) is None:
            missing.append(tool)
    if missing:
        raise KaraokeError("Missing required tools: " + ", ".join(missing))


def _run(cmd: list[str], env: dict | None = None) -> str:
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        raise KaraokeError(
            f"Command failed with return code {proc.returncode}:\n\n"
            + " ".join(cmd)
            + "\n\n"
            + (proc.stdout or "")[-12000:]
        )
    return proc.stdout or ""


def _duration_seconds(path: Path) -> float:
    out = _run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)
    ]).strip()
    try:
        return max(0.0, float(out))
    except Exception:
        return 0.0


def _is_video(path: Path) -> bool:
    out = _run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)
    ]).strip()
    return out == "video"



def filename_identity_hint(path: Path) -> dict[str, str | None]:
    """
    Best-effort local identity hint.

    Supported:
      Song - Artist.ext
      Artist - Song.ext  (the lookup pipeline retries the swapped interpretation)
      Song.ext           (title-only hint; provider search may infer the artist)

    Underscores are treated as spaces.
    """
    stem = Path(path).stem.replace("_", " ").strip()
    stem = re.sub(r"\s+", " ", stem)

    # Remove common generated suffixes.
    stem = re.sub(
        r"\s+(?:-|–|—)\s*(?:karaoke|קריוקי)\s*$",
        "",
        stem,
        flags=re.IGNORECASE,
    ).strip()

    parts = [
        re.sub(r"\s+", " ", p).strip()
        for p in re.split(r"\s+(?:-|–|—)\s+", stem)
        if p.strip()
    ]

    if len(parts) >= 2:
        return {
            "title": parts[0] or None,
            "artist": parts[1] or None,
        }

    # A filename containing only the song title is still useful.
    return {
        "title": stem or None,
        "artist": None,
    }


def probe_media_identity(path: Path) -> dict:
    title = None
    artist = None
    try:
        raw = _run([
            "ffprobe", "-v", "error",
            "-show_entries", "format_tags=title,artist,album_artist",
            "-of", "json", str(path)
        ])
        data = json.loads(raw)
        tags = (data.get("format") or {}).get("tags") or {}
        title = tags.get("title") or tags.get("TITLE")
        artist = (
            tags.get("artist") or tags.get("ARTIST")
            or tags.get("album_artist") or tags.get("ALBUM_ARTIST")
        )
    except Exception:
        pass

    return {
        "title": str(title).strip() if title else None,
        "artist": str(artist).strip() if artist else None,
    }


def resolve_identity_hints(
    manual_title: str | None,
    manual_artist: str | None,
    url_hint: dict[str, str | None],
    metadata: dict[str, str | None],
    filename_hint: dict[str, str | None],
) -> tuple[str | None, str | None, str]:
    manual_title=(manual_title or '').strip() or None; manual_artist=(manual_artist or '').strip() or None
    url_title=(url_hint.get('title') or '').strip() or None; url_artist=(url_hint.get('artist') or '').strip() or None
    metadata_title=(metadata.get('title') or '').strip() or None; metadata_artist=(metadata.get('artist') or '').strip() or None
    file_title=(filename_hint.get('title') or '').strip() or None; file_artist=(filename_hint.get('artist') or '').strip() or None
    title=manual_title or url_title or metadata_title or file_title
    artist=manual_artist or url_artist or metadata_artist or file_artist
    if manual_title or manual_artist: source='manual'
    elif url_title or url_artist: source='musixmatch-url'
    elif metadata_title or metadata_artist: source='metadata'
    elif file_title or file_artist: source='filename'
    else: source='unknown'
    return title, artist, source


def recognize_song_audd(
    audio_path: Path,
    workdir: Path,
    timeout: float = 25.0,
) -> dict | None:
    """
    Identify a song from short audio clips using AudD's official API.

    Requires AUDD_API_TOKEN. V2 tries a few short windows so an instrumental
    intro does not prevent identification. The first successful match wins.
    """
    token = os.environ.get("AUDD_API_TOKEN", "").strip()
    if not token:
        return None

    try:
        import requests
    except Exception:
        return None

    duration = _duration_seconds(audio_path)
    if duration <= 0:
        return None

    clip_len = min(12.0, duration)
    max_attempts = max(
        1,
        min(3, int(os.environ.get("AUDD_MAX_ATTEMPTS", "2") or "2")),
    )

    if duration <= clip_len + 1:
        starts = [0.0]
    else:
        # Favor the body of the song over the intro.
        fractions = [0.18, 0.48, 0.72]
        starts = []
        for frac in fractions[:max_attempts]:
            candidate = duration * frac
            candidate = min(max(0.0, candidate), max(0.0, duration - clip_len - 0.25))
            if not starts or abs(candidate - starts[-1]) > 2.0:
                starts.append(candidate)
        if not starts:
            starts = [0.0]

    for attempt, start_at in enumerate(starts, start=1):
        clip = workdir / f"recognition_clip_{attempt}.mp3"
        try:
            _run([
                "ffmpeg", "-y",
                "-ss", f"{start_at:.2f}",
                "-t", f"{clip_len:.2f}",
                "-i", str(audio_path),
                "-vn",
                "-ac", "2",
                "-ar", "44100",
                "-b:a", "96k",
                str(clip),
            ])

            with clip.open("rb") as f:
                response = requests.post(
                    "https://api.audd.io/",
                    data={
                        "api_token": token,
                        "return": "musicbrainz,apple_music,spotify",
                    },
                    files={"file": (clip.name, f, "audio/mpeg")},
                    timeout=timeout,
                )
            response.raise_for_status()
            data = response.json()

            if data.get("status") != "success":
                continue

            result = data.get("result")
            if not isinstance(result, dict):
                continue

            title = str(result.get("title") or "").strip() or None
            artist = str(result.get("artist") or "").strip() or None
            if not title and not artist:
                continue

            return {
                "title": title,
                "artist": artist,
                "album": str(result.get("album") or "").strip() or None,
                "timecode": str(result.get("timecode") or "").strip() or None,
                "source": "audd",
                "attempt": attempt,
                "clip_start_seconds": round(start_at, 2),
            }
        except Exception:
            continue
        finally:
            try:
                clip.unlink(missing_ok=True)
            except Exception:
                pass

    return None

def _manifest_path(project_dir: Path) -> Path:
    return project_dir / "manifest.json"


def _load_manifest(project_dir: Path) -> dict:
    p = _manifest_path(project_dir)
    if not p.exists():
        return {"stages": {}, "status": "new"}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"stages": {}, "status": "new"}


def _save_manifest(project_dir: Path, manifest: dict) -> None:
    p = _manifest_path(project_dir)
    temp = p.with_suffix(".tmp")
    temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, p)


def _mark_stage(project_dir: Path, stage: str, status: str, **extra) -> None:
    manifest = _load_manifest(project_dir)
    manifest.setdefault("stages", {})
    manifest["stages"][stage] = {
        **manifest["stages"].get(stage, {}),
        "status": status,
        "updated_at": time.time(),
        **extra,
    }
    manifest["status"] = "failed" if status == "failed" else "processing"
    _save_manifest(project_dir, manifest)


def _stage_done(
    project_dir: Path,
    stage: str,
    outputs: Iterable[Path],
    expected_signature: str | None = None,
) -> bool:
    manifest = _load_manifest(project_dir)
    info = manifest.get("stages", {}).get(stage, {})
    if info.get("status") != "done":
        return False
    if expected_signature is not None and info.get("signature") != expected_signature:
        return False
    return all(Path(p).exists() for p in outputs)


def _copy_or_link(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return
    try:
        os.link(src, dst)
    except Exception:
        shutil.copy2(src, dst)


def _cache_key(*parts: str) -> str:
    raw = "|".join(parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def extract_audio(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y", "-i", str(source), "-vn",
        "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(target)
    ])


def _find_stems(folder: Path) -> tuple[Path, Path]:
    wavs = sorted(folder.glob("*.wav"))
    if len(wavs) < 2:
        raise KaraokeError("Separation completed but two WAV stems were not found.")

    vocals = None
    instrumental = None
    for p in wavs:
        low = p.name.lower()
        if "vocal" in low:
            vocals = p
        if any(x in low for x in ("instrument", "no_vocal", "karaoke")):
            instrumental = p

    if vocals is None:
        candidates = [p for p in wavs if "instrument" not in p.name.lower()]
        vocals = candidates[0] if candidates else wavs[0]

    if instrumental is None:
        candidates = [p for p in wavs if p != vocals]
        instrumental = candidates[0]

    return vocals, instrumental


def separate_stems(
    audio_path: Path,
    out_dir: Path,
    perf: dict,
    progress: ProgressCallback | None = None,
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    threads = str(perf.get("threads", 2))
    env.update({
        "OMP_NUM_THREADS": threads,
        "MKL_NUM_THREADS": threads,
        "OPENBLAS_NUM_THREADS": threads,
        "NUMEXPR_NUM_THREADS": threads,
    })

    cmd = [
        "audio-separator", str(audio_path),
        "--model_filename", str(perf.get("separation_model") or SEPARATION_MODEL),
        "--output_format", "WAV",
        "--output_dir", str(out_dir),
        "--chunk_duration", str(perf["chunk_duration"]),
        "--use_soundfile",
        "--mdx_segment_size", str(perf["segment_size"]),
        "--mdx_batch_size", str(perf["batch_size"]),
        "--mdx_overlap", str(perf["overlap"]),
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )

    recent = ""
    seen = (0, 0)
    if proc.stdout is None:
        raise KaraokeError("Could not read audio-separator output.")

    while True:
        ch = proc.stdout.read(1)
        if ch == "" and proc.poll() is not None:
            break
        if not ch:
            time.sleep(0.05)
            continue

        recent = (recent + ch)[-16000:]
        for match in re.finditer(r"(\d+)\s*/\s*(\d+)", recent[-500:]):
            cur, total = int(match.group(1)), int(match.group(2))
            if total > 0 and (cur, total) != seen:
                seen = (cur, total)
                local = min(1.0, cur / total)
                _emit(
                    progress,
                    0.14 + local * 0.31,
                    f"Separating vocals — {cur}/{total}",
                    "Separate",
                )

    code = proc.wait()
    if code != 0:
        raise KaraokeError(
            f"audio-separator failed with return code {code}:\n\n{recent[-12000:]}"
        )
    return _find_stems(out_dir)


def forced_align_worker(
    vocals_path: Path,
    transcript_path: Path,
    lyrics_text: str,
    language: str,
    progress: ProgressCallback | None = None,
) -> dict:
    """
    Align known-correct lyrics directly to the vocal stem.

    This skips ASR text generation entirely. The supplied lyrics are the source
    of truth; WhisperX's language-specific CTC aligner is used only for timing.
    """
    worker = Path(__file__).with_name("forced_align_worker.py")
    lyrics_path = transcript_path.with_suffix(".authoritative.txt")
    lyrics_path.write_text(lyrics_text.strip() + "\n", encoding="utf-8")

    cmd = [
        sys.executable, str(worker),
        "--audio", str(vocals_path),
        "--lyrics", str(lyrics_path),
        "--output", str(transcript_path),
        "--language", language,
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    log: list[str] = []
    if proc.stdout is None:
        raise KaraokeError("Could not read forced-alignment worker output.")

    for raw in proc.stdout:
        line = raw.rstrip()
        log.append(line)
        if line.startswith("KPROGRESS|"):
            try:
                _, frac, msg = line.split("|", 2)
                f = float(frac)
                _emit(
                    progress,
                    0.48 + f * 0.26,
                    msg,
                    "Sync",
                )
            except Exception:
                pass

    code = proc.wait()
    if code != 0 or not transcript_path.exists():
        raise KaraokeError(
            "Exact-text alignment failed.\n\n" + "\n".join(log[-120:])
        )

    return json.loads(transcript_path.read_text(encoding="utf-8"))



def transcribe_worker(
    vocals_path: Path,
    transcript_path: Path,
    language: str,
    perf: dict,
    progress: ProgressCallback | None = None,
) -> dict:
    worker = Path(__file__).with_name("transcribe_worker.py")
    cmd = [
        sys.executable, str(worker),
        "--audio", str(vocals_path),
        "--output", str(transcript_path),
        "--model", str(perf["whisper_model"]),
        "--language", language,
        "--align", "yes" if perf["align"] else "no",
        "--engine", str(perf.get("transcribe_engine", "whisperx")),
        "--batch-size", str(perf.get("asr_batch_size", 1)),
        "--cpu-threads", str(perf.get("threads", 2)),
        "--vad", "yes" if perf.get("vad_filter", True) else "no",
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    log: list[str] = []
    if proc.stdout is None:
        raise KaraokeError("Could not read transcription worker output.")

    for raw in proc.stdout:
        line = raw.rstrip()
        log.append(line)
        if line.startswith("KPROGRESS|"):
            try:
                _, frac, msg = line.split("|", 2)
                f = float(frac)
                friendly = msg.replace("transcription", "timing anchors").replace("Transcription", "Timing anchors").replace("Fast timing pass", "Finding timing anchors")
                _emit(
                    progress,
                    0.48 + f * 0.26,
                    friendly,
                    "Sync",
                )
            except Exception:
                pass

    code = proc.wait()
    if code != 0 or not transcript_path.exists():
        raise KaraokeError(
            "Transcription worker failed.\n\n" + "\n".join(log[-120:])
        )

    return json.loads(transcript_path.read_text(encoding="utf-8"))


def _punctuation_end(text: str) -> bool:
    return bool(re.search(r"[.!?。！？…,:;]$", text.strip()))


def group_words_into_lines(
    words: list[dict],
    max_words: int = 10,
    max_chars: int = 44,
    pause_threshold: float = 0.60,
) -> list[dict]:
    lines: list[dict] = []
    current: list[dict] = []

    def flush():
        nonlocal current
        if not current:
            return
        text = " ".join(x["text"] for x in current).strip()
        lines.append({
            "start": float(current[0]["start"]),
            "end": float(current[-1]["end"]),
            "text": text,
            "words": current,
        })
        current = []

    previous_end = None
    for word in words:
        token = (word.get("text") or "").strip()
        if not token:
            continue
        item = {
            "text": token,
            "start": float(word["start"]),
            "end": float(word["end"]),
        }

        if current and previous_end is not None:
            pause = item["start"] - previous_end
            next_text = " ".join([x["text"] for x in current] + [token])
            if (
                pause >= pause_threshold
                or len(current) >= max_words
                or len(next_text) > max_chars
            ):
                flush()

        current.append(item)
        previous_end = item["end"]

        if _punctuation_end(token) and len(current) >= 3:
            flush()

    flush()
    return lines


def _equal_word_timings(text: str, start: float, end: float) -> list[dict]:
    tokens = text.split()
    if not tokens:
        return []
    duration = max(0.1, end - start)
    step = duration / len(tokens)
    out = []
    for i, token in enumerate(tokens):
        s = start + i * step
        e = end if i == len(tokens) - 1 else start + (i + 1) * step
        out.append({"text": token, "start": s, "end": e})
    return out



def _distribute_lines_over_timeline(
    lyrics_text: str,
    words: list[dict],
    language: str,
) -> list[dict]:
    """
    Last-resort timing for user-supplied authoritative lyrics.

    The text remains exactly user-controlled; timing is distributed
    proportionally over the transcription timeline when word alignment is weak.
    """
    lines_text = canonical_lines(lyrics_text, language)
    if not lines_text:
        return []

    if words:
        start = float(words[0].get("start", 0.0))
        end = float(words[-1].get("end", start + max(1.0, len(lines_text))))
    else:
        start = 0.0
        end = max(1.0, len(lines_text) * 3.0)

    total_duration = max(0.5, end - start)
    weights = [max(1, len(line.split())) for line in lines_text]
    total_weight = max(1, sum(weights))

    cursor = start
    result: list[dict] = []
    for i, (line, weight) in enumerate(zip(lines_text, weights)):
        duration = total_duration * weight / total_weight
        line_end = end if i == len(lines_text) - 1 else min(end, cursor + duration)
        result.append({
            "start": cursor,
            "end": max(cursor + 0.1, line_end),
            "text": line,
            "words": _equal_word_timings(line, cursor, max(cursor + 0.1, line_end)),
            "corrected": True,
            "lyrics_source": "manual",
        })
        cursor = max(cursor + 0.1, line_end)

    return result


def save_manual_lyrics_text(
    project_dir: Path,
    lyrics_text: str,
    language: str | None = None,
) -> tuple[Path, float]:
    """
    Save user-provided lyrics as authoritative text and invalidate only
    subtitle/render stages.

    First tries to align the supplied lyrics to Whisper timestamps. If the
    transcription is too inaccurate to align, it preserves the user's text
    and falls back to proportional timing.
    """
    lyrics_text = (lyrics_text or "").strip()
    if not lyrics_text:
        raise KaraokeError("Manual lyrics are empty.")

    transcript_path = project_dir / "transcript.json"
    payload = {}
    if transcript_path.exists():
        payload = json.loads(transcript_path.read_text(encoding="utf-8"))

    words = payload.get("words", []) or []
    detected = (
        language
        or payload.get("language")
        or _load_manifest(project_dir).get("detected_language")
        or "auto"
    )
    if detected == "unknown":
        detected = "auto"

    aligned = align_verified_lyrics(
        lyrics_text,
        words,
        detected,
        source="manual",
        minimum_confidence=0.15,
    )

    if aligned.lines:
        lines = aligned.lines
        confidence = float(aligned.confidence)
    else:
        lines = _distribute_lines_over_timeline(lyrics_text, words, detected)
        confidence = 0.0

    if not lines:
        raise KaraokeError("Could not build karaoke lines from manual lyrics.")

    path = project_dir / "corrected_lines.json"
    path.write_text(
        json.dumps(lines, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    (project_dir / "manual_lyrics.txt").write_text(
        lyrics_text + "\n",
        encoding="utf-8",
    )

    manifest = _load_manifest(project_dir)
    manifest["lyrics_verified"] = True
    manifest["lyrics_source"] = "manual"
    manifest["lyrics_confidence"] = confidence
    manifest["status"] = "ready_to_render"

    for stage in ("lines", "subtitles", "render"):
        if stage in manifest.get("stages", {}):
            manifest["stages"][stage]["status"] = "invalidated"

    _save_manifest(project_dir, manifest)
    return path, confidence


def load_manual_lyrics_text(project_dir: Path) -> str:
    manual = project_dir / "manual_lyrics.txt"
    if manual.exists():
        return manual.read_text(encoding="utf-8").strip()

    rows = load_editable_lines(project_dir)
    return "\n".join(str(row.get("text", "")).strip() for row in rows if row.get("text"))


def save_corrected_lines(project_dir: Path, edited: list[dict]) -> Path:
    path = project_dir / "corrected_lines.json"
    normalized = []
    for row in edited:
        text = str(row.get("text", "")).strip()
        if not text:
            continue
        start = float(row.get("start", 0.0))
        end = max(start + 0.1, float(row.get("end", start + 0.1)))
        normalized.append({
            "start": start,
            "end": end,
            "text": text,
            "words": _equal_word_timings(text, start, end),
            "corrected": True,
        })

    path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    manifest = _load_manifest(project_dir)
    for stage in ("subtitles", "render"):
        if stage in manifest.get("stages", {}):
            manifest["stages"][stage]["status"] = "invalidated"
    manifest["status"] = "ready_to_render"
    _save_manifest(project_dir, manifest)
    return path


def load_editable_lines(project_dir: Path) -> list[dict]:
    corrected = project_dir / "corrected_lines.json"
    base = project_dir / "lines.json"
    path = corrected if corrected.exists() else base
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        {
            "start": round(float(x["start"]), 2),
            "end": round(float(x["end"]), 2),
            "text": x.get("text", ""),
        }
        for x in data
    ]



def detect_first_vocal_onset(vocals_path: Path) -> float:
    """
    Estimate where vocal content begins.

    Uses FFmpeg silencedetect only as a calibration hint. The value is shown
    to the user before render and is not blindly trusted as a final sync.
    """
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats",
        "-i", str(vocals_path),
        "-af", "silencedetect=noise=-35dB:d=0.18",
        "-f", "null", "-"
    ]
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    output = proc.stdout or ""

    # If the file begins with silence, the first silence_end is our best hint.
    match = re.search(r"silence_end:\s*([0-9.]+)", output)
    if match:
        try:
            return max(0.0, float(match.group(1)))
        except Exception:
            pass

    return 0.0


def first_lyric_time(lines: list[dict]) -> float:
    if not lines:
        return 0.0
    try:
        return max(0.0, float(lines[0].get("start", 0.0)))
    except Exception:
        return 0.0


def suggested_sync_offset(vocals_path: Path, lines: list[dict]) -> float:
    """
    Return a suggested global shift in seconds.

    Positive moves lyrics later; negative moves them earlier.
    """
    vocal_start = detect_first_vocal_onset(vocals_path)
    lyric_start = first_lyric_time(lines)
    return round(vocal_start - lyric_start, 2)


def apply_sync_offset(lines: list[dict], offset_seconds: float) -> list[dict]:
    offset = float(offset_seconds or 0.0)
    adjusted: list[dict] = []

    for line in lines:
        item = json.loads(json.dumps(line, ensure_ascii=False))
        start = max(0.0, float(item.get("start", 0.0)) + offset)
        end = max(start + 0.03, float(item.get("end", start + 0.1)) + offset)
        item["start"] = start
        item["end"] = end

        new_words = []
        for word in item.get("words", []) or []:
            w = dict(word)
            ws = max(0.0, float(w.get("start", start)) + offset)
            we = max(ws + 0.03, float(w.get("end", ws + 0.1)) + offset)
            w["start"] = ws
            w["end"] = we
            new_words.append(w)

        if new_words:
            item["words"] = new_words

        adjusted.append(item)

    return adjusted



def normalize_karaoke_line_timing(
    lines: list[dict],
    min_gap: float = 0.04,
    min_duration: float = 0.28,
    max_line_duration: float = 9.0,
) -> list[dict]:
    """
    Make adjacent lyric lines non-overlapping without cascading timing drift.

    The line's own start/end remain authoritative. Word timestamps are clamped
    into the line instead of expanding the line to accommodate stray word times.
    """
    if not lines:
        return []

    normalized: list[dict] = []

    for raw in lines:
        item = json.loads(json.dumps(raw, ensure_ascii=False))
        try:
            line_start = max(0.0, float(item.get("start", 0.0)))
        except Exception:
            line_start = 0.0
        try:
            line_end = float(item.get("end", line_start + 1.0))
        except Exception:
            line_end = line_start + 1.0

        line_end = max(line_start + min_duration, line_end)
        line_end = min(line_end, line_start + max_line_duration)

        item["start"] = line_start
        item["end"] = line_end
        normalized.append(item)

    # Preserve canonical lyric order. On overlap, shorten the previous line
    # first; only move the next line if shortening is impossible.
    for i in range(len(normalized) - 1):
        current = normalized[i]
        nxt = normalized[i + 1]

        current_start = float(current["start"])
        current_end = float(current["end"])
        next_start = float(nxt["start"])
        next_end = float(nxt["end"])

        if next_start < current_start:
            next_start = current_start + min_duration
            nxt["start"] = next_start

        if current_end > next_start - min_gap:
            target_end = next_start - min_gap

            if target_end >= current_start + min_duration:
                current["end"] = target_end
            else:
                current["end"] = current_start + min_duration
                required_next = float(current["end"]) + min_gap
                if next_start < required_next:
                    nxt["start"] = required_next
                    nxt["end"] = max(
                        required_next + min_duration,
                        next_end,
                    )

    # Clamp all words inside their owning line and preserve word order.
    for item in normalized:
        line_start = float(item["start"])
        line_end = float(item["end"])
        words = item.get("words") or []
        clean_words = []
        cursor = line_start

        for word in words:
            w = dict(word)
            try:
                ws = float(w.get("start", cursor))
            except Exception:
                ws = cursor
            try:
                we = float(w.get("end", ws + 0.08))
            except Exception:
                we = ws + 0.08

            ws = max(line_start, cursor, ws)
            if ws >= line_end:
                break

            we = min(line_end, max(ws + 0.03, we))
            if we <= ws:
                continue

            w["start"] = ws
            w["end"] = we
            clean_words.append(w)
            cursor = we

        if clean_words:
            item["words"] = clean_words

    return normalized



def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def _ass_color(rgb: str, alpha: str = "00") -> str:
    rgb = rgb.lstrip("#").upper()
    if len(rgb) != 6:
        rgb = "FFFFFF"
    r, g, b = rgb[:2], rgb[2:4], rgb[4:6]
    return f"&H{alpha}{b}{g}{r}&"


def _font_path(font_name: str) -> str | None:
    try:
        out = _run(["fc-match", "-f", "%{file}", font_name]).strip()
        return out if out and Path(out).exists() else None
    except Exception:
        return None


def _measure_words(words: list[str], font_name: str, font_size: int) -> tuple[list[float], float]:
    try:
        from PIL import ImageFont
        font_path = _font_path(font_name)
        if font_path:
            font = ImageFont.truetype(font_path, font_size)
            widths = [max(8.0, float(font.getlength(w))) for w in words]
            space = max(8.0, float(font.getlength(" ")))
            return widths, space
    except Exception:
        pass

    # Conservative fallback if Pillow/fontconfig measurement is unavailable.
    widths = [max(12.0, len(w) * font_size * 0.56) for w in words]
    return widths, font_size * 0.34


def _ass_escape(text: str) -> str:
    return (
        str(text)
        .replace("\\", r"\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("\n", " ")
    )


# Karaoke guidance timings. These are intentionally renderer-only offsets:
# the authoritative line/word timestamps written by the sync engine remain unchanged.
HIGHLIGHT_LEAD_SECONDS = 0.28
VOCAL_GAP_THRESHOLD_SECONDS = 3.0
COUNTDOWN_SECONDS = 3
NEXT_LINE_PREVIEW_SCALE = 0.68

# Cache fingerprints: changing either value forces only the cheap downstream
# stages to rebuild. Older projects therefore receive renderer improvements
# without repeating source separation or Precision Sync.
SUBTITLE_RENDERER_SIGNATURE = "singer-guidance-4.9.3"
VIDEO_RENDER_SIGNATURE = "video-fidelity-4.9.3+" + SUBTITLE_RENDERER_SIGNATURE


def _line_words(line: dict) -> list[dict]:
    line_start = float(line.get("start", 0.0))
    line_end = max(line_start, float(line.get("end", line_start)))
    words = line.get("words") or _equal_word_timings(
        line.get("text", ""),
        line_start,
        line_end,
    )
    return [w for w in words if str(w.get("text") or "").strip()]


def _vocal_start(line: dict) -> float:
    # Never let a stale/malformed word timestamp drag a vocal entry earlier
    # than the canonical line start. This is especially important for intro
    # countdown detection on reused/edited projects.
    line_start = max(0.0, float(line.get("start", 0.0)))
    words = _line_words(line)
    if words:
        return max(line_start, float(words[0].get("start", line_start)))
    return line_start


def _vocal_end(line: dict) -> float:
    words = _line_words(line)
    if words:
        return max(0.0, float(words[-1].get("end", line.get("end", 0.0))))
    return max(0.0, float(line.get("end", 0.0)))


def _plain_line_text(line: dict, rtl: bool) -> str:
    words = _line_words(line)
    text = " ".join(_ass_escape(str(w["text"]).strip()) for w in words)
    if rtl and text:
        text = "\u200F" + text
    return text


def create_ass(
    lines: list[dict],
    target: Path,
    language: str,
    style: dict,
) -> None:
    """
    Karaoke renderer with singer guidance.

    * LTR: render one complete line and overlay the same line for active-word
      highlighting. libass controls spacing, so words cannot collide.
    * RTL: preserve independent word positioning for bidi safety.
    * Active-word colour is advanced by 280 ms without modifying canonical
      sync timestamps.
    * The upcoming line is shown smaller under the active line for short gaps.
    * Long instrumental gaps get a 3-2-1 countdown ending at the true next
      vocal onset.
    """
    font = style["font"]
    font_size = int(style["font_size"])
    preview_size = max(24, int(round(font_size * NEXT_LINE_PREVIEW_SCALE)))
    countdown_size = max(58, int(round(font_size * 1.25)))
    primary = _ass_color(style["primary"])
    highlight = _ass_color(style["highlight"])
    outline = _ass_color(style["outline"])
    # About 75% opaque. ASS alpha: 00=opaque, FF=transparent.
    preview_colour = _ass_color(style["primary"], "40")
    outline_width = int(style["outline_width"])
    shadow = int(style["shadow"])

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Karaoke,{font},{font_size},{primary},{highlight},{outline},&H80000000,-1,0,0,0,100,100,0,0,1,{outline_width},{shadow},5,0,0,0,1
Style: KaraokeActive,{font},{font_size},{highlight},{highlight},{outline},&H80000000,-1,0,0,0,100,100,0,0,1,{outline_width},{shadow},5,0,0,0,1
Style: KaraokePreview,{font},{preview_size},{preview_colour},{preview_colour},{outline},&H90000000,0,0,0,0,100,100,0,0,1,{max(1, outline_width - 1)},{max(0, shadow - 1)},5,0,0,0,1
Style: KaraokeCountdown,{font},{countdown_size},{highlight},{highlight},{outline},&H80000000,-1,0,0,0,100,100,0,0,1,{outline_width},{shadow},5,0,0,0,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""

    events: list[str] = []
    rtl = language in RTL_LANGUAGES
    center_x = 640.0
    # Raised from the old 635 px baseline to make room for next-line preview.
    main_y = 565.0
    preview_y = 635.0
    countdown_y = 590.0

    usable_lines = [line for line in lines if _line_words(line)]

    for line_index, line in enumerate(usable_lines):
        line_start = max(0.0, float(line["start"]))
        line_end = max(line_start + 0.04, float(line["end"]))
        # Renderer-only pre-roll: gives the first word the same 280 ms visual
        # lead as every later word without mutating stored sync timestamps.
        display_start = max(0.0, line_start - HIGHLIGHT_LEAD_SECONDS)

        words = _line_words(line)
        if not words:
            continue
        tokens = [str(w["text"]).strip() for w in words]

        if not rtl:
            safe_tokens = [_ass_escape(t) for t in tokens]
            line_text = " ".join(safe_tokens)
            tags = rf"{{\an5\pos({center_x:.1f},{main_y:.1f})}}"

            # White base line.
            events.append(
                f"Dialogue: 0,{_ass_time(display_start)},{_ass_time(line_end)},"
                f"Karaoke,,0,0,0,,{tags}{line_text}"
            )

            # Same exact line layout; only active word changes color.
            for active_idx, word in enumerate(words):
                raw_ws = float(word.get("start", line_start))
                raw_we = max(raw_ws + 0.04, float(word.get("end", raw_ws + 0.25)))
                # Lead the visual cue slightly so singers perceive it on time.
                ws = max(display_start, raw_ws - HIGHLIGHT_LEAD_SECONDS)
                we = min(line_end, max(ws + 0.04, raw_we - HIGHLIGHT_LEAD_SECONDS))
                if we <= ws:
                    continue

                parts = []
                for idx, token in enumerate(safe_tokens):
                    if idx == active_idx:
                        parts.append(
                            rf"{{\1c{highlight}}}{token}{{\1c{primary}}}"
                        )
                    else:
                        parts.append(token)

                overlay = " ".join(parts)
                events.append(
                    f"Dialogue: 1,{_ass_time(ws)},{_ass_time(we)},"
                    f"Karaoke,,0,0,0,,{tags}{overlay}"
                )
        else:
            # RTL word renderer with extra spacing.
            widths, space = _measure_words(tokens, font, font_size)
            widths = [w * 1.12 for w in widths]
            space = max(space * 1.35, font_size * 0.38)
            total_width = sum(widths) + space * max(0, len(widths) - 1)

            scale_x = 100
            if total_width > 1160:
                scale_x = max(72, int(1160 / total_width * 100))
                factor = scale_x / 100.0
                widths = [w * factor for w in widths]
                space *= factor
                total_width = sum(widths) + space * max(0, len(widths) - 1)

            positions = []
            edge = center_x + total_width / 2.0
            for width in widths:
                positions.append(edge - width / 2.0)
                edge -= width + space

            for idx, word in enumerate(words):
                token = _ass_escape(word["text"])
                x = positions[idx]
                tags = rf"{{\an5\pos({x:.1f},{main_y:.1f})\fscx{scale_x}}}"

                events.append(
                    f"Dialogue: 0,{_ass_time(display_start)},{_ass_time(line_end)},"
                    f"Karaoke,,0,0,0,,{tags}{token}"
                )

                raw_ws = float(word.get("start", line_start))
                raw_we = max(raw_ws + 0.04, float(word.get("end", raw_ws + 0.25)))
                ws = max(display_start, raw_ws - HIGHLIGHT_LEAD_SECONDS)
                we = min(line_end, max(ws + 0.04, raw_we - HIGHLIGHT_LEAD_SECONDS))
                if we > ws:
                    events.append(
                        f"Dialogue: 1,{_ass_time(ws)},{_ass_time(we)},"
                        f"KaraokeActive,,0,0,0,,{tags}{token}"
                    )

        # Singer confidence: show the next line for the whole active line
        # whenever vocals continue normally. During a genuine instrumental
        # break, suppress the preview and use the countdown instead.
        if line_index + 1 < len(usable_lines):
            next_line = usable_lines[line_index + 1]
            next_vocal_start = _vocal_start(next_line)
            current_vocal_end = _vocal_end(line)
            vocal_gap = max(0.0, next_vocal_start - current_vocal_end)

            if vocal_gap < VOCAL_GAP_THRESHOLD_SECONDS:
                preview_text = _plain_line_text(next_line, rtl)
                if preview_text:
                    preview_start = display_start
                    if line_end > preview_start:
                        preview_tags = rf"{{\an5\pos({center_x:.1f},{preview_y:.1f})}}"
                        events.append(
                            f"Dialogue: 0,{_ass_time(preview_start)},{_ass_time(line_end)},"
                            f"KaraokePreview,,0,0,0,,{preview_tags}{preview_text}"
                        )

    # Count down to the first vocal entry and to any later vocal return after a
    # genuine instrumental break. Use the first word timestamp, not line lead-in.
    countdown_targets: list[float] = []
    if usable_lines:
        first_vocal = _vocal_start(usable_lines[0])
        if first_vocal >= VOCAL_GAP_THRESHOLD_SECONDS:
            countdown_targets.append(first_vocal)

        for current, nxt in zip(usable_lines, usable_lines[1:]):
            next_vocal = _vocal_start(nxt)
            gap = next_vocal - _vocal_end(current)
            if gap >= VOCAL_GAP_THRESHOLD_SECONDS:
                countdown_targets.append(next_vocal)

    countdown_tags = rf"{{\an5\pos({center_x:.1f},{countdown_y:.1f})}}"
    for target_time in countdown_targets:
        visual_target = max(0.0, target_time - HIGHLIGHT_LEAD_SECONDS)
        countdown_start = max(0.0, visual_target - COUNTDOWN_SECONDS)
        for step in range(COUNTDOWN_SECONDS, 0, -1):
            step_start = max(countdown_start, visual_target - step)
            step_end = min(visual_target, visual_target - step + 1.0)
            if step_end <= step_start:
                continue
            events.append(
                f"Dialogue: 2,{_ass_time(step_start)},{_ass_time(step_end)},"
                f"KaraokeCountdown,,0,0,0,,{countdown_tags}{step}"
            )

    target.write_text(header + "\n".join(events) + "\n", encoding="utf-8")



def _parse_ffmpeg_time(value: str) -> float:
    try:
        h, m, s = value.strip().split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception:
        return 0.0


def probe_video_rotation(path: Path) -> int:
    """
    Return the display rotation declared by the source video as one of:
    0, 90, 180, 270.

    Phone videos may store pixels in one orientation and rely on a display
    matrix/rotate tag. V2 reads that metadata and bakes the rotation into the
    rendered pixels so the final MP4 looks correct in every player.
    """
    try:
        raw = _run([
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries",
            "stream_tags=rotate:stream_side_data=rotation",
            "-of", "json",
            str(path),
        ])
        data = json.loads(raw)
        streams = data.get("streams") or []
        if not streams:
            return 0

        stream = streams[0] or {}
        rotation = None

        tags = stream.get("tags") or {}
        if tags.get("rotate") is not None:
            try:
                rotation = float(tags.get("rotate"))
            except Exception:
                rotation = None

        if rotation is None:
            for side in stream.get("side_data_list") or []:
                if side.get("rotation") is not None:
                    try:
                        rotation = float(side.get("rotation"))
                        break
                    except Exception:
                        pass

        if rotation is None:
            return 0

        # Normalize arbitrary/negative rotation values to the closest right angle.
        normalized = int(round(rotation / 90.0) * 90) % 360
        return normalized if normalized in {0, 90, 180, 270} else 0
    except Exception:
        return 0


def rotation_filter(rotation: int) -> str | None:
    """
    FFmpeg filter that converts source display rotation into actual pixels.

    With -noautorotate enabled:
      90  -> clockwise transpose
      180 -> horizontal + vertical flip
      270 -> counter-clockwise transpose
    """
    rotation = int(rotation) % 360
    if rotation == 90:
        return "transpose=clock"
    if rotation == 180:
        return "hflip,vflip"
    if rotation == 270:
        return "transpose=cclock"
    return None


def render_video(
    source: Path,
    instrumental: Path,
    subtitles: Path,
    target: Path,
    use_original_video: bool,
    style: dict,
    progress: ProgressCallback | None = None,
    rotation_override: int | None = None,
    video_preset: str = "medium",
    video_crf: int = 14,
) -> None:
    duration = max(_duration_seconds(instrumental), 1.0)
    escaped_subtitles = str(subtitles).replace(":", r"\:")
    subtitle_filter = f"ass={escaped_subtitles}:shaping=complex"

    if use_original_video and _is_video(source):
        source_rotation = (
            int(rotation_override) % 360
            if rotation_override is not None
            else probe_video_rotation(source)
        )
        rotate_filter = rotation_filter(source_rotation)
        video_filters = [x for x in ("setpts=PTS-STARTPTS", rotate_filter, subtitle_filter) if x]
        vf = ",".join(video_filters)

        cmd = [
            "ffmpeg", "-y",
            "-noautorotate",
            "-i", str(source),
            "-i", str(instrumental),
            "-map", "0:v:0", "-map", "1:a:0",
            "-vf", vf,
            "-metadata:s:v:0", "rotate=0",
            "-c:v", "libx264", "-preset", str(video_preset), "-crf", str(video_crf), "-tune", "film",
            "-pix_fmt", "yuv420p",
            "-af", "asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0",
            "-c:a", "aac", "-b:a", "256k",
            "-movflags", "+faststart",
            "-shortest",
            "-progress", "pipe:1", "-nostats",
            str(target),
        ]
    else:
        bg = style.get("background", "101018")
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"color=c=0x{bg}:s=1280x720:r=30",
            "-i", str(instrumental),
            "-vf", f"setpts=PTS-STARTPTS,{subtitle_filter}",
            "-c:v", "libx264", "-preset", str(video_preset), "-crf", str(video_crf), "-tune", "film",
            "-pix_fmt", "yuv420p",
            "-af", "asetpts=PTS-STARTPTS,aresample=async=1:first_pts=0",
            "-c:a", "aac", "-b:a", "256k",
            "-movflags", "+faststart",
            "-shortest",
            "-progress", "pipe:1", "-nostats",
            str(target),
        ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    log = []
    if proc.stdout is None:
        raise KaraokeError("Could not read ffmpeg progress.")

    for line in proc.stdout:
        line = line.strip()
        log.append(line)
        if line.startswith("out_time="):
            sec = _parse_ffmpeg_time(line.split("=", 1)[1])
            frac = min(1.0, sec / duration)
            _emit(
                progress,
                0.88 + frac * 0.11,
                f"Rendering video — {int(frac * 100)}%",
                "Render",
            )

    code = proc.wait()
    if code != 0:
        raise KaraokeError("FFmpeg render failed.\n\n" + "\n".join(log[-120:]))


def build_karaoke(
    source_path: Path,
    project_dir: Path,
    config: BuildConfig,
    progress: ProgressCallback | None = None,
    stop_before_render: bool = False,
) -> BuildResult:
    ensure_tools()
    project_dir.mkdir(parents=True, exist_ok=True)
    resolved = config.resolved()
    perf = resolved["performance"]
    style = resolved["style"]
    resumed: list[str] = []

    source_hash = file_sha256(source_path)
    separation_model = str(perf.get("separation_model") or SEPARATION_MODEL)
    stem_key = _cache_key(
        source_hash,
        separation_model,
        config.performance_mode,
        str(perf["segment_size"]),
        str(perf["batch_size"]),
        str(perf["overlap"]),
    )
    audio = project_dir / "source_audio.wav"
    stems_dir = project_dir / "stems"
    vocals = stems_dir / "vocals.wav"
    instrumental = stems_dir / "instrumental.wav"
    transcript = project_dir / "transcript.json"
    lines_path = project_dir / "lines.json"
    corrected_path = project_dir / "corrected_lines.json"
    subtitles = project_dir / "karaoke.ass"

    identity = probe_media_identity(source_path)

    project_meta = get_project(project_dir.name) or {}
    original_filename = str(project_meta.get("original_filename") or "").strip()
    filename_hint_path = Path(original_filename) if original_filename else source_path
    filename_hint = filename_identity_hint(filename_hint_path)
    mxm_hint = parse_musixmatch_url_hint(config.musixmatch_url_hint) if config.musixmatch_url_hint else {"title": None, "artist": None}
    resolved_title, resolved_artist, identity_source = resolve_identity_hints(
        manual_title=config.song_title, manual_artist=config.artist_name, url_hint=mxm_hint, metadata=identity, filename_hint=filename_hint
    )
    title = _safe_name(resolved_title or source_path.stem)
    output_filename = None
    video = None

    manifest = _load_manifest(project_dir)
    manifest["config"] = asdict(config)
    manifest["source_hash"] = source_hash
    manifest["status"] = "processing"
    _save_manifest(project_dir, manifest)

    try:
        # 1. AUDIO
        if _stage_done(project_dir, "audio", [audio]):
            resumed.append("audio")
            _emit(progress, 0.08, "Using extracted audio", "Extract")
        else:
            _emit(progress, 0.03, "Extracting audio", "Extract")
            extract_audio(source_path, audio)
            _mark_stage(project_dir, "audio", "done", path=str(audio))
            _emit(progress, 0.10, "Audio extracted", "Extract")

        # 1B. SONG IDENTIFICATION
        identity_manifest = _load_manifest(project_dir)
        saved_identity = identity_manifest.get("song_identity") or {}
        if not resolved_title:
            resolved_title = saved_identity.get("title") or None
        if not resolved_artist:
            resolved_artist = saved_identity.get("artist") or None

        manual_identity_complete = bool((config.song_title or "").strip() and (config.artist_name or "").strip())
        audd_configured = bool(os.environ.get("AUDD_API_TOKEN", "").strip())
        if config.use_song_recognition and audd_configured and not manual_identity_complete:
            _emit(progress, 0.11, "Identifying / validating song from audio", "Identify song")
            recognized = recognize_song_audd(audio, project_dir)
            if recognized:
                if not (config.song_title or "").strip(): resolved_title = recognized.get("title") or resolved_title
                if not (config.artist_name or "").strip(): resolved_artist = recognized.get("artist") or resolved_artist
                identity_source = "audd"
                identity_manifest = _load_manifest(project_dir)
                identity_manifest["song_identity"] = {**recognized, "resolved_title": resolved_title, "resolved_artist": resolved_artist, "identity_source": identity_source}
                _save_manifest(project_dir, identity_manifest)
                _emit(progress, 0.12, f"Song identified: {resolved_title or 'Unknown'} — {resolved_artist or 'Unknown'}", "Identify song")
            else:
                _emit(progress, 0.12, "Song recognition did not find a match", "Identify song")
        else:
            identity_manifest = _load_manifest(project_dir)
            identity_manifest["song_identity"] = {"title": resolved_title, "artist": resolved_artist, "resolved_title": resolved_title, "resolved_artist": resolved_artist, "identity_source": identity_source, "source": identity_source}
            _save_manifest(project_dir, identity_manifest)

        # Final filename is resolved only after metadata/audio recognition.
        title = _safe_name(resolved_title or source_path.stem)
        if config.custom_output_filename:
            output_filename = sanitize_output_filename(config.custom_output_filename)
        else:
            output_filename = build_output_filename(
                source_path, resolved_title, resolved_artist
            )
        video = project_dir / output_filename

        # 1C. RELIABLE LYRICS BEFORE HEAVY PROCESSING
        # Simple Flow: never spend minutes on separation/transcription before
        # we know we have trustworthy text to synchronize.
        prefetched_candidate = None
        prefetched_lookup = None
        authoritative_text = None
        authoritative_source = None

        if config.manual_lyrics_text and config.manual_lyrics_text.strip():
            authoritative_text = config.manual_lyrics_text.strip()
            authoritative_source = "manual"
            prefetched_lookup = {
                "provider": "manual",
                "status": "ok",
                "message": "Lyrics supplied manually by the user.",
            }
        elif (
            config.use_verified_lyrics
            and resolved_title
        ):
            _emit(progress, 0.115, "Searching reliable lyrics", "Find lyrics")

            if resolved_artist:
                lookup_result = fetch_licensed_lyrics_with_status(
                    title=resolved_title,
                    artist=resolved_artist,
                    language=config.language,
                    transcript_hint=None,
                    duration=_duration_seconds(audio),
                )
            else:
                lookup_result = fetch_lyricsovh_title_only(
                    title=resolved_title,
                    timeout=10.0,
                )
                if lookup_result.candidate is not None:
                    resolved_title = (
                        lookup_result.candidate.title or resolved_title
                    )
                    resolved_artist = lookup_result.candidate.artist
                    identity_source = "filename-title-only"

                    identity_manifest = _load_manifest(project_dir)
                    identity_manifest["song_identity"] = {
                        "title": resolved_title,
                        "artist": resolved_artist,
                        "resolved_title": resolved_title,
                        "resolved_artist": resolved_artist,
                        "identity_source": identity_source,
                        "source": identity_source,
                    }
                    _save_manifest(project_dir, identity_manifest)

            # Filename identities are inherently ambiguous:
            #   "Song - Artist" and "Artist - Song" are both common.
            # If the first interpretation fails, try the swapped identity
            # before asking the user for manual lyrics.
            if (
                lookup_result.candidate is None
                and identity_source == "filename"
                and resolved_title
                and resolved_artist
            ):
                swapped = fetch_licensed_lyrics_with_status(
                    title=resolved_artist,
                    artist=resolved_title,
                    language=config.language,
                    transcript_hint=None,
                    duration=_duration_seconds(audio),
                )
                if swapped.candidate is not None:
                    resolved_title, resolved_artist = (
                        resolved_artist,
                        resolved_title,
                    )
                    identity_source = "filename-swapped"
                    lookup_result = swapped

                    identity_manifest = _load_manifest(project_dir)
                    identity_manifest["song_identity"] = {
                        "title": resolved_title,
                        "artist": resolved_artist,
                        "resolved_title": resolved_title,
                        "resolved_artist": resolved_artist,
                        "identity_source": identity_source,
                        "source": identity_source,
                    }
                    _save_manifest(project_dir, identity_manifest)

            prefetched_candidate = lookup_result.candidate
            prefetched_lookup = {
                "provider": lookup_result.provider,
                "status": lookup_result.status,
                "message": lookup_result.message,
            }
            if prefetched_candidate is not None:
                authoritative_text = prefetched_candidate.lyrics.strip()
                authoritative_source = prefetched_candidate.source

                # Provider identity can be better than filename metadata.
                resolved_title = prefetched_candidate.title or resolved_title
                resolved_artist = prefetched_candidate.artist or resolved_artist

                # Enrich plain lyrics with exact-duration LRCLIB sync when available.
                media_duration = _duration_seconds(audio)
                if (
                    not prefetched_candidate.synced_lyrics
                    and resolved_title
                    and resolved_artist
                ):
                    lrclib_sync = fetch_lrclib_lyrics(
                        title=resolved_title,
                        artist=resolved_artist,
                        duration=media_duration,
                        timeout=8.0,
                    )
                    if (
                        lrclib_sync.candidate is not None
                        and lrclib_sync.candidate.synced_lyrics
                        and synced_lyrics_compatible(
                            lrclib_sync.candidate.provider_duration,
                            media_duration,
                        )
                    ):
                        prefetched_candidate = lrclib_sync.candidate
                        authoritative_text = prefetched_candidate.lyrics.strip()
                        authoritative_source = prefetched_candidate.source
                        prefetched_lookup = {
                            "provider": "lrclib",
                            "status": "ok",
                            "message": "LRCLIB supplied duration-matched synced lyrics.",
                        }

                # Final filename only after provider identity is known.
                title = _safe_name(
                    resolved_title
                    or Path(original_filename or source_path.name).stem
                )
                if config.custom_output_filename:
                    output_filename = sanitize_output_filename(
                        config.custom_output_filename
                    )
                else:
                    output_filename = build_output_filename(
                        source_path, resolved_title, resolved_artist
                    )
                video = project_dir / output_filename

                identity_manifest = _load_manifest(project_dir)
                identity_manifest["song_identity"] = {
                    **(identity_manifest.get("song_identity") or {}),
                    "title": resolved_title,
                    "artist": resolved_artist,
                    "resolved_title": resolved_title,
                    "resolved_artist": resolved_artist,
                    "identity_source": (
                        (identity_manifest.get("song_identity") or {}).get(
                            "identity_source"
                        )
                        or identity_source
                    ),
                }
                _save_manifest(project_dir, identity_manifest)

        lookup_manifest = _load_manifest(project_dir)
        lookup_manifest["lyrics_lookup"] = prefetched_lookup or {
            "provider": "none",
            "status": "not_found",
            "message": "No reliable lyrics were found.",
        }
        _save_manifest(project_dir, lookup_manifest)

        if not authoritative_text:
            # Stop immediately. No separation. No Whisper. No guessed draft.
            source_rotation = (
                probe_video_rotation(source_path)
                if _is_video(source_path)
                else 0
            )
            manifest = _load_manifest(project_dir)
            manifest["status"] = "awaiting_lyrics_input"
            manifest["song_title"] = resolved_title
            manifest["artist_name"] = resolved_artist
            manifest["output_filename"] = video.name if video else None
            manifest["lyrics_verified"] = False
            manifest["lyrics_source"] = "none"
            manifest["lyrics_confidence"] = 0.0
            manifest["review"] = {
                "lyrics_lookup": manifest.get("lyrics_lookup") or {},
                "identity_source": (
                    (manifest.get("song_identity") or {}).get("identity_source")
                    or identity_source
                ),
                "source_rotation": source_rotation,
            }
            manifest["outputs"] = {
                "audio": str(audio),
            }
            _save_manifest(project_dir, manifest)

            upsert_project({
                "project_id": project_dir.name,
                "project_dir": str(project_dir),
                "title": title,
                "source_path": str(source_path),
                "source_hash": source_hash,
                "status": "awaiting_lyrics_input",
                "detected_language": config.language,
                "song_title": resolved_title,
                "artist_name": resolved_artist,
                "output_filename": video.name if video else None,
                "lyrics_verified": False,
                "lyrics_source": "none",
                "lyrics_confidence": 0.0,
                "config": asdict(config),
                "review": manifest["review"],
                "outputs": manifest["outputs"],
            })

            _emit(
                progress,
                0.12,
                "Reliable lyrics not found — paste lyrics to continue",
                "Find lyrics",
            )

            return BuildResult(
                project_id=project_dir.name,
                project_dir=project_dir,
                video_path=video,
                instrumental_path=instrumental,
                subtitles_path=subtitles,
                transcript_path=transcript,
                lines_path=lines_path,
                detected_language=config.language,
                resumed_stages=resumed,
                lyrics_verified=False,
                lyrics_source="none",
                lyrics_confidence=0.0,
            )

        _emit(
            progress,
            0.13,
            f"Lyrics ready from {authoritative_source}",
            "Find lyrics",
        )

        # 2. STEMS with shared cache
        cached_stems = CACHE_ROOT / "stems" / stem_key
        cached_vocals = cached_stems / "vocals.wav"
        cached_inst = cached_stems / "instrumental.wav"

        if _stage_done(project_dir, "stems", [vocals, instrumental]):
            resumed.append("stems")
            _emit(progress, 0.45, "Using existing stems", "Separate")
        elif cached_vocals.exists() and cached_inst.exists():
            _copy_or_link(cached_vocals, vocals)
            _copy_or_link(cached_inst, instrumental)
            _mark_stage(project_dir, "stems", "done", cache_hit=True)
            resumed.append("stems-cache")
            _emit(progress, 0.45, "Loaded stems from cache", "Separate")
        else:
            _emit(progress, 0.12, "Separating vocals / instrumental", "Separate")
            temp_dir = project_dir / "_separator_output"
            shutil.rmtree(temp_dir, ignore_errors=True)
            temp_dir.mkdir(parents=True, exist_ok=True)

            raw_vocals, raw_inst = separate_stems(audio, temp_dir, perf, progress)
            stems_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(raw_vocals), str(vocals))
            shutil.move(str(raw_inst), str(instrumental))
            shutil.rmtree(temp_dir, ignore_errors=True)

            cached_stems.mkdir(parents=True, exist_ok=True)
            _copy_or_link(vocals, cached_vocals)
            _copy_or_link(instrumental, cached_inst)
            _mark_stage(project_dir, "stems", "done", cache_hit=False)
            _emit(progress, 0.46, "Vocals separated", "Separate")

        # 2B. TIMING STRATEGY
        # Text is authoritative. AI may only provide time anchors.
        media_duration = _duration_seconds(audio)
        provider_synced_lines: list[dict] = []

        if (
            prefetched_candidate is not None
            and prefetched_candidate.synced_lyrics
            and synced_lyrics_compatible(
                prefetched_candidate.provider_duration,
                media_duration,
            )
        ):
            provider_synced_lines = parse_synced_lyrics_lines(
                prefetched_candidate.synced_lyrics,
                media_duration=media_duration,
                language=config.language,
            )

        lyrics_fingerprint = hashlib.sha256(
            authoritative_text.encode("utf-8")
        ).hexdigest()

        if provider_synced_lines:
            sync_strategy = "provider-synced-v1"
            transcript_key = _cache_key(
                source_hash,
                sync_strategy,
                lyrics_fingerprint,
                str(round(media_duration, 2)),
            )
        else:
            sync_strategy = "asr-anchor-v1"
            anchor_perf = dict(perf)
            anchor_perf["transcribe_engine"] = "faster-whisper"
            anchor_perf["align"] = False
            anchor_perf["whisper_model"] = (
                "base" if config.performance_mode == "Fast" else "small"
            )
            anchor_perf["asr_batch_size"] = max(
                4, int(anchor_perf.get("asr_batch_size", 8))
            )
            anchor_perf["vad_filter"] = True
            transcript_key = _cache_key(
                stem_key,
                sync_strategy,
                str(anchor_perf["whisper_model"]),
                config.language,
            )

        # 3. TIMING ANCHORS / PROVIDER SYNC
        cached_transcript = CACHE_ROOT / "transcripts" / f"{transcript_key}.json"
        payload = None

        if transcript.exists():
            try:
                existing = json.loads(transcript.read_text(encoding="utf-8"))
                mode = str(existing.get("alignment_mode") or "")
                compatible = (
                    (sync_strategy == "provider-synced-v1" and mode == "provider-synced-lines")
                    or
                    (sync_strategy == "asr-anchor-v1" and mode == "faster-whisper-word-timestamps")
                )
                if compatible:
                    payload = existing
                    resumed.append("timing")
                    _emit(progress, 0.74, "Using existing timing anchors", "Sync")
            except Exception:
                payload = None

        if payload is None and cached_transcript.exists():
            try:
                cached_payload = json.loads(
                    cached_transcript.read_text(encoding="utf-8")
                )
                mode = str(cached_payload.get("alignment_mode") or "")
                compatible = (
                    (sync_strategy == "provider-synced-v1" and mode == "provider-synced-lines")
                    or
                    (sync_strategy == "asr-anchor-v1" and mode == "faster-whisper-word-timestamps")
                )
                if compatible:
                    _copy_or_link(cached_transcript, transcript)
                    payload = cached_payload
                    resumed.append("timing-cache")
                    _emit(progress, 0.74, "Loaded timing anchors from cache", "Sync")
            except Exception:
                payload = None

        if payload is None:
            if provider_synced_lines:
                _emit(progress, 0.50, "Using duration-matched provider timing", "Sync")
                payload = {
                    "language": (
                        config.language if config.language != "auto" else "unknown"
                    ),
                    "alignment_mode": "provider-synced-lines",
                    "engine": "provider",
                    "segments": provider_synced_lines,
                    "words": [
                        word
                        for line in provider_synced_lines
                        for word in (line.get("words") or [])
                    ],
                }
                transcript.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            else:
                _emit(
                    progress,
                    0.48,
                    "Finding timing anchors in the vocal track",
                    "Sync",
                )
                payload = transcribe_worker(
                    vocals,
                    transcript,
                    config.language,
                    anchor_perf,
                    progress,
                )

            cached_transcript.parent.mkdir(parents=True, exist_ok=True)
            _copy_or_link(transcript, cached_transcript)
            _mark_stage(
                project_dir,
                "transcript",
                "done",
                cache_hit=False,
                language=payload.get("language", "unknown"),
                alignment_mode=payload.get("alignment_mode", "unknown"),
                sync_strategy=sync_strategy,
            )
            _emit(progress, 0.75, "Timing anchors ready", "Sync")

        detected_language = payload.get("language", "unknown")


        # 4. VERIFIED LYRICS + LINES
        lyrics_verified = False
        lyrics_source = authoritative_source or "manual"
        lyrics_confidence = 0.0
        current_sync_version = (
            "provider-synced-v1"
            if payload.get("alignment_mode") == "provider-synced-lines"
            else "asr-anchor-v1"
        )

        if corrected_path.exists():
            lines = json.loads(corrected_path.read_text(encoding="utf-8"))
            lyrics_source = "manual-correction"
            lyrics_verified = True
            lyrics_confidence = 1.0
            _emit(progress, 0.82, "Using corrected lyrics", "Sync")
        else:
            reuse_lines = False
            if _stage_done(project_dir, "lines", [lines_path]):
                manifest_now = _load_manifest(project_dir)
                line_info = manifest_now.get("stages", {}).get("lines", {})
                if line_info.get("sync_version") == current_sync_version:
                    reuse_lines = True

            if reuse_lines:
                resumed.append("lines")
                lines = json.loads(lines_path.read_text(encoding="utf-8"))
                manifest_now = _load_manifest(project_dir)
                line_info = manifest_now.get("stages", {}).get("lines", {})
                lyrics_verified = bool(line_info.get("lyrics_verified"))
                lyrics_source = str(
                    line_info.get("lyrics_source")
                    or authoritative_source
                    or "manual"
                )
                lyrics_confidence = float(
                    line_info.get("lyrics_confidence") or 0.0
                )
                _emit(progress, 0.82, "Using synchronized lines", "Sync")
            else:
                _emit(
                    progress,
                    0.76,
                    "Mapping reliable lyrics to timing anchors",
                    "Sync",
                )

                if payload.get("alignment_mode") == "provider-synced-lines":
                    lines = []
                    for seg in payload.get("segments", []) or []:
                        lines.append({
                            "text": str(seg.get("text") or "").strip(),
                            "start": float(seg.get("start", 0.0)),
                            "end": float(seg.get("end", 0.0)),
                            "words": [
                                {
                                    "text": str(
                                        w.get("text") or w.get("word") or ""
                                    ).strip(),
                                    "start": float(
                                        w.get("start", seg.get("start", 0.0))
                                    ),
                                    "end": float(
                                        w.get("end", seg.get("end", 0.0))
                                    ),
                                }
                                for w in (seg.get("words") or [])
                                if str(
                                    w.get("text") or w.get("word") or ""
                                ).strip()
                            ],
                            "verified": True,
                            "timing_source": "provider-synced",
                        })
                    lyrics_confidence = 1.0
                else:
                    anchor_words = payload.get("words", []) or []
                    detected = (
                        detected_language
                        if detected_language != "unknown"
                        else config.language
                    )
                    aligned = align_verified_lyrics(
                        authoritative_text,
                        anchor_words,
                        detected,
                        source=authoritative_source or "manual",
                        title=resolved_title,
                        artist=resolved_artist,
                        minimum_confidence=0.18,
                    )

                    if aligned.lines:
                        lines = aligned.lines
                        lyrics_confidence = float(aligned.confidence)
                    else:
                        lines = _distribute_lines_over_timeline(
                            authoritative_text,
                            anchor_words,
                            detected,
                        )
                        lyrics_confidence = 0.0

                lyrics_verified = True
                lyrics_source = authoritative_source or "manual"
                lines = normalize_karaoke_line_timing(lines)

                # New timing invalidates downstream subtitle/render artifacts.
                subtitles.unlink(missing_ok=True)
                if video is not None:
                    video.unlink(missing_ok=True)

                lines_path.write_text(
                    json.dumps(lines, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                _mark_stage(
                    project_dir,
                    "lines",
                    "done",
                    count=len(lines),
                    lyrics_verified=True,
                    lyrics_source=lyrics_source,
                    lyrics_confidence=lyrics_confidence,
                    sync_version=current_sync_version,
                )
                _emit(progress, 0.82, "Lyrics synchronized", "Sync")

            if authoritative_source == "manual":
                (project_dir / "manual_lyrics.txt").write_text(
                    authoritative_text.strip() + "\n",
                    encoding="utf-8",
                )
            else:
                canonical_payload = {
                    "title": resolved_title,
                    "artist": resolved_artist,
                    "source": authoritative_source,
                    "confidence": lyrics_confidence,
                    "lyrics": authoritative_text,
                }
                (project_dir / "canonical_lyrics.json").write_text(
                    json.dumps(
                        canonical_payload,
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )

        if stop_before_render:
            review_text = "\n".join(
                str(line.get("text", "")).strip()
                for line in lines
                if str(line.get("text", "")).strip()
            )
            review_path = project_dir / "review_lyrics.txt"
            review_path.write_text(review_text + "\n", encoding="utf-8")

            sync_hint = suggested_sync_offset(vocals, lines)
            source_rotation = probe_video_rotation(source_path) if _is_video(source_path) else 0

            manifest = _load_manifest(project_dir)
            manifest["status"] = "awaiting_review"
            manifest["detected_language"] = detected_language
            manifest["song_title"] = resolved_title
            manifest["artist_name"] = resolved_artist
            manifest["output_filename"] = video.name if video else None
            manifest["lyrics_verified"] = lyrics_verified
            manifest["lyrics_source"] = lyrics_source
            manifest["lyrics_confidence"] = lyrics_confidence
            manifest["review"] = {
                "lyrics_text_path": str(review_path),
                "suggested_sync_offset": sync_hint,
                "source_rotation": source_rotation,
                "trusted_required": True,
                "trusted_lyrics_found": True,
                "must_correct_before_render": False,
                "lyrics_lookup": manifest.get("lyrics_lookup") or {},
                "identity_source": ((_load_manifest(project_dir).get("song_identity") or {}).get("identity_source") or identity_source),
            }
            manifest["outputs"] = {
                "instrumental": str(instrumental),
                "transcript": str(transcript),
                "lines": str(lines_path),
            }
            _save_manifest(project_dir, manifest)

            upsert_project({
                "project_id": project_dir.name,
                "project_dir": str(project_dir),
                "title": title,
                "source_path": str(source_path),
                "source_hash": source_hash,
                "status": "awaiting_review",
                "detected_language": detected_language,
                "song_title": resolved_title,
                "artist_name": resolved_artist,
                "output_filename": video.name if video else None,
                "lyrics_verified": lyrics_verified,
                "lyrics_source": lyrics_source,
                "lyrics_confidence": lyrics_confidence,
                "config": asdict(config),
                "review": manifest["review"],
                "outputs": manifest["outputs"],
            })

            _emit(progress, 0.84, "Lyrics ready for review", "Review lyrics")

            return BuildResult(
                project_id=project_dir.name,
                project_dir=project_dir,
                video_path=video,
                instrumental_path=instrumental,
                subtitles_path=subtitles,
                transcript_path=transcript,
                lines_path=lines_path,
                detected_language=detected_language,
                resumed_stages=resumed,
                lyrics_verified=lyrics_verified,
                lyrics_source=lyrics_source,
                lyrics_confidence=lyrics_confidence,
            )
        # 5. SUBTITLES
        if _stage_done(
            project_dir,
            "subtitles",
            [subtitles],
            expected_signature=SUBTITLE_RENDERER_SIGNATURE,
        ):
            resumed.append("subtitles")
            _emit(progress, 0.86, "Using existing subtitles", "Subtitles")
        else:
            _emit(progress, 0.83, "Creating karaoke subtitles", "Subtitles")
            create_ass(lines, subtitles, detected_language, style)
            _mark_stage(
                project_dir,
                "subtitles",
                "done",
                path=str(subtitles),
                signature=SUBTITLE_RENDERER_SIGNATURE,
            )
            _emit(progress, 0.87, "Subtitles ready", "Subtitles")

        # 6. RENDER
        if video is None:
            raise KaraokeError("Final output path was not resolved.")
        if _stage_done(
            project_dir,
            "render",
            [video],
            expected_signature=VIDEO_RENDER_SIGNATURE,
        ):
            resumed.append("render")
            _emit(progress, 1.0, "Karaoke ready", "Done")
        else:
            _emit(progress, 0.88, "Rendering final karaoke video", "Render")
            render_video(
                source_path,
                instrumental,
                subtitles,
                video,
                config.use_original_video,
                style,
                progress,
                rotation_override=config.rotation_override,
                video_preset=str(perf.get("render_preset", "veryfast")),
                video_crf=int(perf.get("render_crf", 20)),
            )
            _mark_stage(
                project_dir,
                "render",
                "done",
                path=str(video),
                signature=VIDEO_RENDER_SIGNATURE,
            )
            _emit(progress, 1.0, "Karaoke ready", "Done")

        manifest = _load_manifest(project_dir)
        manifest["status"] = "done"
        manifest["detected_language"] = detected_language
        manifest["song_title"] = resolved_title
        manifest["artist_name"] = resolved_artist
        manifest["song_identity"] = (_load_manifest(project_dir).get("song_identity") or {})
        manifest["output_filename"] = video.name
        manifest["lyrics_verified"] = lyrics_verified
        manifest["lyrics_source"] = lyrics_source
        manifest["lyrics_confidence"] = lyrics_confidence
        manifest["outputs"] = {
            "video": str(video),
            "instrumental": str(instrumental),
            "subtitles": str(subtitles),
            "transcript": str(transcript),
            "lines": str(lines_path),
        }
        _save_manifest(project_dir, manifest)

        upsert_project({
            "project_id": project_dir.name,
            "project_dir": str(project_dir),
            "title": title,
            "source_path": str(source_path),
            "source_hash": source_hash,
            "status": "done",
            "detected_language": detected_language,
            "song_title": resolved_title,
            "artist_name": resolved_artist,
            "output_filename": video.name,
            "lyrics_verified": lyrics_verified,
            "lyrics_source": lyrics_source,
            "lyrics_confidence": lyrics_confidence,
            "config": asdict(config),
            "outputs": manifest["outputs"],
        })

        return BuildResult(
            project_id=project_dir.name,
            project_dir=project_dir,
            video_path=video,
            instrumental_path=instrumental,
            subtitles_path=subtitles,
            transcript_path=transcript,
            lines_path=lines_path,
            detected_language=detected_language,
            resumed_stages=resumed,
            lyrics_verified=lyrics_verified,
            lyrics_source=lyrics_source,
            lyrics_confidence=lyrics_confidence,
        )

    except Exception as exc:
        manifest = _load_manifest(project_dir)
        manifest["status"] = "failed"
        manifest["error"] = f"{type(exc).__name__}: {exc}"
        _save_manifest(project_dir, manifest)

        upsert_project({
            "project_id": project_dir.name,
            "project_dir": str(project_dir),
            "title": _safe_name(source_path.stem),
            "source_path": str(source_path),
            "source_hash": source_hash,
            "status": "failed",
            "config": asdict(config),
            "error": str(exc),
        })
        raise




def finalize_reviewed_project(
    project_dir: Path,
    source_path: Path,
    config: BuildConfig,
    approved_lyrics_text: str,
    sync_offset_seconds: float = 0.0,
    rotation_override: int | None = None,
    progress: ProgressCallback | None = None,
) -> BuildResult:
    """
    Finalize an awaiting-review project.

    Reuses stems/transcription, saves the approved lyrics as authoritative,
    applies a global sync offset, then renders subtitles + final MP4 only.
    """
    project_dir = Path(project_dir)
    source_path = Path(source_path)

    instrumental = project_dir / "stems" / "instrumental.wav"
    transcript = project_dir / "transcript.json"
    lines_path = project_dir / "lines.json"
    corrected_path = project_dir / "corrected_lines.json"
    subtitles = project_dir / "karaoke.ass"
    vocals = project_dir / "stems" / "vocals.wav"

    if not instrumental.exists() or not transcript.exists():
        raise KaraokeError("Project is missing prepared audio/transcription files.")

    payload = json.loads(transcript.read_text(encoding="utf-8"))
    detected_language = payload.get("language") or "unknown"

    _emit(progress, 0.05, "Saving approved lyrics", "Review lyrics")
    _, alignment_confidence = save_manual_lyrics_text(
        project_dir,
        approved_lyrics_text,
        language=detected_language,
    )

    if not corrected_path.exists():
        raise KaraokeError("Approved lyric lines were not created.")

    lines = json.loads(corrected_path.read_text(encoding="utf-8"))
    adjusted_lines = apply_sync_offset(lines, sync_offset_seconds)
    adjusted_lines = normalize_karaoke_line_timing(adjusted_lines)

    # Persist the exact reviewed timing used for final render.
    reviewed_lines_path = project_dir / "reviewed_lines.json"
    reviewed_lines_path.write_text(
        json.dumps(adjusted_lines, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    resolved = config.resolved()
    style = resolved["style"]

    identity = probe_media_identity(source_path)
    manifest_before = _load_manifest(project_dir)
    resolved_title = (
        config.song_title
        or manifest_before.get("song_title")
        or identity.get("title")
        or ""
    ).strip() or None
    resolved_artist = (
        config.artist_name
        or manifest_before.get("artist_name")
        or identity.get("artist")
        or ""
    ).strip() or None

    if config.custom_output_filename:
        output_filename = sanitize_output_filename(config.custom_output_filename)
    else:
        output_filename = build_output_filename(
            source_path, resolved_title, resolved_artist
        )

    video = project_dir / output_filename

    _emit(progress, 0.35, "Creating reviewed subtitles", "Subtitles")
    create_ass(adjusted_lines, subtitles, detected_language, style)
    _mark_stage(
        project_dir,
        "subtitles",
        "done",
        path=str(subtitles),
        reviewed=True,
        signature=SUBTITLE_RENDERER_SIGNATURE,
    )

    _emit(progress, 0.55, "Rendering reviewed karaoke video", "Render")
    render_video(
        source_path,
        instrumental,
        subtitles,
        video,
        config.use_original_video,
        style,
        progress,
        rotation_override=rotation_override,
        video_preset=str(resolved["performance"].get("render_preset", "veryfast")),
        video_crf=int(resolved["performance"].get("render_crf", 20)),
    )
    _mark_stage(
        project_dir,
        "render",
        "done",
        path=str(video),
        reviewed=True,
        signature=VIDEO_RENDER_SIGNATURE,
    )

    manifest = _load_manifest(project_dir)
    manifest["status"] = "done"
    manifest["detected_language"] = detected_language
    manifest["song_title"] = resolved_title
    manifest["artist_name"] = resolved_artist
    manifest["output_filename"] = video.name
    manifest["lyrics_verified"] = True
    manifest["lyrics_source"] = "manual-review"
    manifest["lyrics_confidence"] = alignment_confidence
    manifest["review"] = {
        **(manifest.get("review") or {}),
        "approved": True,
        "sync_offset_seconds": float(sync_offset_seconds),
        "rotation_override": rotation_override,
    }
    manifest["outputs"] = {
        "video": str(video),
        "instrumental": str(instrumental),
        "subtitles": str(subtitles),
        "transcript": str(transcript),
        "lines": str(reviewed_lines_path),
    }
    _save_manifest(project_dir, manifest)

    upsert_project({
        "project_id": project_dir.name,
        "project_dir": str(project_dir),
        "title": _safe_name(resolved_title or source_path.stem),
        "source_path": str(source_path),
        "source_hash": file_sha256(source_path),
        "status": "done",
        "detected_language": detected_language,
        "song_title": resolved_title,
        "artist_name": resolved_artist,
        "output_filename": video.name,
        "lyrics_verified": True,
        "lyrics_source": "manual-review",
        "lyrics_confidence": alignment_confidence,
        "config": asdict(config),
        "review": manifest["review"],
        "outputs": manifest["outputs"],
    })

    _emit(progress, 1.0, "Karaoke ready", "Done")

    return BuildResult(
        project_id=project_dir.name,
        project_dir=project_dir,
        video_path=video,
        instrumental_path=instrumental,
        subtitles_path=subtitles,
        transcript_path=transcript,
        lines_path=reviewed_lines_path,
        detected_language=detected_language,
        resumed_stages=["audio", "stems", "transcript"],
        lyrics_verified=True,
        lyrics_source="manual-review",
        lyrics_confidence=alignment_confidence,
    )



def create_project_from_file(
    file_path: Path,
    config: BuildConfig,
) -> tuple[str, Path, Path]:
    """
    Create/reuse a project from a file that already exists on the server.

    This bypasses Streamlit's browser upload path, which is useful in
    development environments such as GitHub Codespaces where the forwarded
    proxy may reject larger HTTP request bodies before Streamlit sees them.
    """
    file_path = Path(file_path).expanduser().resolve()
    if not file_path.exists() or not file_path.is_file():
        raise KaraokeError(f"Input media file does not exist: {file_path}")

    source_hash = file_sha256(file_path)
    settings_key = hashlib.sha256(
        json.dumps(asdict(config), sort_keys=True).encode("utf-8")
    ).hexdigest()[:10]
    project_id = f"{source_hash[:14]}-{settings_key}"
    project_dir = PROJECTS_ROOT / project_id
    project_dir.mkdir(parents=True, exist_ok=True)

    suffix = file_path.suffix.lower() or ".bin"
    source = project_dir / f"source{suffix}"

    if not source.exists() or source.stat().st_size != file_path.stat().st_size:
        if source.exists():
            source.unlink()
        try:
            os.link(file_path, source)
        except Exception:
            shutil.copy2(file_path, source)

    upsert_project({
        "project_id": project_id,
        "project_dir": str(project_dir),
        "title": _safe_name(file_path.stem),
        "original_filename": file_path.name,
        "source_path": str(source),
        "source_hash": source_hash,
        "status": _load_manifest(project_dir).get("status", "new"),
        "config": asdict(config),
        "input_mode": "server-file",
        "original_source_path": str(file_path),
    })
    return project_id, project_dir, source

def create_project_from_upload(
    filename: str,
    data: bytes,
    config: BuildConfig,
) -> tuple[str, Path, Path]:
    source_hash = bytes_sha256(data)
    settings_key = hashlib.sha256(
        json.dumps(asdict(config), sort_keys=True).encode("utf-8")
    ).hexdigest()[:10]
    project_id = f"{source_hash[:14]}-{settings_key}"
    project_dir = PROJECTS_ROOT / project_id
    project_dir.mkdir(parents=True, exist_ok=True)

    suffix = Path(filename).suffix.lower() or ".bin"
    source = project_dir / f"source{suffix}"
    if not source.exists() or source.stat().st_size != len(data):
        source.write_bytes(data)

    upsert_project({
        "project_id": project_id,
        "project_dir": str(project_dir),
        "title": _safe_name(Path(filename).stem),
        "original_filename": filename,
        "source_path": str(source),
        "source_hash": source_hash,
        "status": _load_manifest(project_dir).get("status", "new"),
        "config": asdict(config),
    })
    return project_id, project_dir, source
