from __future__ import annotations

import gc
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from rapidfuzz.fuzz import ratio

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma"}

class KaraokeError(RuntimeError):
    pass

def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NO_WINDOW
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=creationflags,
    )
    if proc.returncode != 0:
        raise KaraokeError(
            "Command failed:\n" + " ".join(cmd) + "\n\n" + (proc.stdout or "")[-6000:]
        )
    return proc

def ensure_ffmpeg() -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise KaraokeError(
            "FFmpeg/ffprobe were not found in PATH. "
            "On Windows run setup_windows.ps1 once, then reopen the terminal."
        )

def probe_duration(path: Path) -> float:
    ensure_ffmpeg()
    out = run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "json", str(path)
    ]).stdout
    return float(json.loads(out)["format"]["duration"])

def extract_audio(input_path: Path, wav_out: Path) -> Path:
    ensure_ffmpeg()
    run([
        "ffmpeg", "-y", "-i", str(input_path), "-vn",
        "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(wav_out)
    ])
    return wav_out

SEPARATION_MODELS = {
    "Fast / balanced": "UVR-MDX-NET-Inst_HQ_3.onnx",
    "Higher quality / slower": "model_bs_roformer_ep_317_sdr_12.9755.yaml",
}

def separate_stems(audio_path: Path, out_dir: Path, quality: str) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    model = SEPARATION_MODELS[quality]
    run([
        sys.executable, "-m", "audio_separator", str(audio_path),
        "--model_filename", model, "--output_format", "WAV",
        "--output_dir", str(out_dir)
    ])
    wavs = sorted(out_dir.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
    if len(wavs) < 2:
        raise KaraokeError("Audio Separator did not produce two WAV stems.")
    instrumental = next((p for p in wavs if any(x in p.name.lower() for x in ("instrumental","no_vocals","karaoke"))), None)
    vocals = next((p for p in wavs if "vocal" in p.name.lower()), None)
    if instrumental is None or vocals is None:
        instrumental, vocals = wavs[0], wavs[1]
    if instrumental == vocals:
        raise KaraokeError("Could not distinguish instrumental and vocal stems.")
    return instrumental, vocals

@dataclass
class TimedWord:
    text: str
    start: float
    end: float

def _cleanup_gpu() -> None:
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass

def transcribe_word_times(vocals_path: Path, language: str, model_name: str) -> tuple[list[TimedWord], str]:
    import torch
    import whisperx

    device = "cuda" if torch.cuda.is_available() else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"
    batch_size = 16 if device == "cuda" else 4

    kwargs = {"compute_type": compute_type}
    if language != "auto":
        kwargs["language"] = language

    model = whisperx.load_model(model_name, device, **kwargs)
    audio = whisperx.load_audio(str(vocals_path))
    result = model.transcribe(audio, batch_size=batch_size)
    detected = language if language != "auto" else result.get("language", "en")

    del model
    _cleanup_gpu()

    try:
        align_model, metadata = whisperx.load_align_model(language_code=detected, device=device)
        aligned = whisperx.align(
            result["segments"], align_model, metadata, audio, device,
            return_char_alignments=False
        )
        del align_model
        _cleanup_gpu()
    except Exception as exc:
        raise KaraokeError(
            f"WhisperX alignment failed for language '{detected}'. "
            f"Try choosing Hebrew or English explicitly. Original error: {exc}"
        ) from exc

    words = []
    for segment in aligned.get("segments", []):
        for item in segment.get("words", []) or []:
            text = str(item.get("word", "")).strip()
            start, end = item.get("start"), item.get("end")
            if text and start is not None and end is not None and end > start:
                words.append(TimedWord(text, float(start), float(end)))
    if len(words) < 3:
        raise KaraokeError("Too few timed words were detected.")
    return words, detected

HEBREW_DIACRITICS = re.compile(r"[\u0591-\u05C7]")
HEBREW_FINAL_MAP = str.maketrans({"ך":"כ","ם":"מ","ן":"נ","ף":"פ","ץ":"צ"})

@dataclass
class LyricWord:
    original: str
    normalized: str
    line_index: int
    token_index: int
    start: Optional[float] = None
    end: Optional[float] = None

def normalize_token(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = HEBREW_DIACRITICS.sub("", text).translate(HEBREW_FINAL_MAP).lower()
    return "".join(
        ch for ch in text
        if unicodedata.category(ch).startswith(("L","N")) or ch in {"'","’"}
    )

def parse_lyrics(lyrics: str) -> tuple[list[LyricWord], list[list[int]]]:
    words, lines = [], []
    for line_i, line in enumerate(lyrics.replace("\r\n","\n").split("\n")):
        line = line.strip()
        if not line:
            continue
        indices = []
        for token_i, token in enumerate(re.findall(r"\S+", line)):
            norm = normalize_token(token)
            if not norm:
                continue
            idx = len(words)
            words.append(LyricWord(token, norm, line_i, token_i))
            indices.append(idx)
        if indices:
            lines.append(indices)
    if len(words) < 3:
        raise KaraokeError("Please provide at least a few words of lyrics.")
    return words, lines

def _sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return 1.0 if a == b else ratio(a, b)/100.0

def sequence_align(lyrics: list[LyricWord], asr_words: list[TimedWord]) -> dict[int,int]:
    A = [w.normalized for w in lyrics]
    B = [normalize_token(w.text) for w in asr_words]
    n, m = len(A), len(B)
    gap = 0.78
    dp = [[0.0]*(m+1) for _ in range(n+1)]
    back = [[0]*(m+1) for _ in range(n+1)]

    for i in range(1,n+1):
        dp[i][0] = i*gap; back[i][0] = 2
    for j in range(1,m+1):
        dp[0][j] = j*gap; back[0][j] = 3

    for i in range(1,n+1):
        for j in range(1,m+1):
            sub = dp[i-1][j-1] + 1.05*(1.0-_sim(A[i-1],B[j-1]))
            up = dp[i-1][j] + gap
            left = dp[i][j-1] + gap
            dp[i][j], back[i][j] = min((sub,1),(up,2),(left,3), key=lambda x:x[0])

    mapping = {}
    i,j = n,m
    while i>0 or j>0:
        step = back[i][j]
        if step == 1:
            if _sim(A[i-1],B[j-1]) >= 0.48:
                mapping[i-1] = j-1
            i -= 1; j -= 1
        elif step == 2:
            i -= 1
        else:
            j -= 1
    return mapping

def apply_and_interpolate_timings(
    lyrics: list[LyricWord],
    asr_words: list[TimedWord],
    mapping: dict[int,int],
    audio_duration: float,
) -> None:
    for li, ai in mapping.items():
        lyrics[li].start = asr_words[ai].start
        lyrics[li].end = asr_words[ai].end

    anchors = [i for i,w in enumerate(lyrics) if w.start is not None and w.end is not None]
    if len(anchors) < max(3, int(len(lyrics)*0.18)):
        raise KaraokeError(
            "The supplied lyrics do not match the detected vocals closely enough. "
            "Check the song/version and the lyrics."
        )

    durations = [lyrics[i].end-lyrics[i].start for i in anchors]
    durations = [d for d in durations if 0.08 <= d <= 2.5]
    typical = sorted(durations)[len(durations)//2] if durations else 0.42
    typical = min(max(typical,0.22),0.8)

    first = anchors[0]
    if first > 0:
        end_t = max(0.0, float(lyrics[first].start)-0.04)
        start_t = max(0.0, end_t-typical*first)
        step = (end_t-start_t)/max(first,1)
        for k in range(first):
            s = start_t+k*step
            e = start_t+(k+1)*step-0.02
            lyrics[k].start = s
            lyrics[k].end = max(s+0.08,e)

    for a,b in zip(anchors,anchors[1:]):
        missing = b-a-1
        if missing <= 0:
            continue
        left = float(lyrics[a].end)
        right = float(lyrics[b].start)
        if right <= left:
            right = left+typical*(missing+1)
        step = (right-left)/(missing+1)
        for offset in range(1,missing+1):
            idx = a+offset
            center = left+step*offset
            s = max(left,center-step*0.42)
            e = min(right,center+step*0.42)
            lyrics[idx].start = s
            lyrics[idx].end = max(s+0.08,e)

    last = anchors[-1]
    if last < len(lyrics)-1:
        s0 = float(lyrics[last].end)
        remaining = len(lyrics)-last-1
        step = max(0.15, min(typical, max(0.15,(audio_duration-s0)/max(remaining,1))))
        for offset in range(1,remaining+1):
            idx = last+offset
            s = min(audio_duration-0.05, s0+(offset-1)*step)
            e = min(audio_duration, s+step*0.9)
            lyrics[idx].start = s
            lyrics[idx].end = max(s+0.05,e)

    prev_start = 0.0
    for w in lyrics:
        assert w.start is not None and w.end is not None
        w.start = max(prev_start,min(float(w.start),audio_duration-0.03))
        w.end = min(audio_duration,max(w.start+0.05,float(w.end)))
        prev_start = w.start

def ass_time(seconds: float) -> str:
    cs = int(round(max(0.0,seconds)*100))
    h, rem = divmod(cs,360000)
    m, rem = divmod(rem,6000)
    s, c = divmod(rem,100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"

def ass_escape(text: str) -> str:
    return text.replace("{","（").replace("}","）").replace("\\N"," ")

def contains_hebrew(text: str) -> bool:
    return any("\u0590" <= ch <= "\u05FF" for ch in text)

def _styled_line(words: list[LyricWord], active_idx: int, rtl: bool) -> str:
    parts = []
    for i,w in enumerate(words):
        token = ass_escape(w.original)
        if i == active_idx:
            parts.append(r"{\c&H00D7FF&\b1}"+token+r"{\c&HFFFFFF&\b0}")
        else:
            parts.append(token)
    text = " ".join(parts)
    if rtl:
        text = "\u200F"+text
    return text

def create_ass(
    lyrics: list[LyricWord],
    line_word_indices: list[list[int]],
    out_path: Path,
    font_name: str = "Arial",
    font_size: int = 58,
) -> Path:
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,{font_name},{font_size},&H00FFFFFF,&H0000FFFF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,3,1,2,60,60,95,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for line_indices in line_word_indices:
        line_words = [lyrics[i] for i in line_indices]
        if not line_words:
            continue
        rtl = contains_hebrew(" ".join(w.original for w in line_words))
        line_start = max(0.0,float(line_words[0].start)-0.28)
        line_end = float(line_words[-1].end)+0.25

        first_start = float(line_words[0].start)
        if first_start > line_start:
            plain = " ".join(ass_escape(w.original) for w in line_words)
            if rtl:
                plain = "\u200F"+plain
            events.append(
                f"Dialogue: 0,{ass_time(line_start)},{ass_time(first_start)},Karaoke,,0,0,0,,{plain}"
            )

        for local_i,word in enumerate(line_words):
            start = float(word.start)
            if local_i+1 < len(line_words):
                end = max(float(word.end),float(line_words[local_i+1].start))
            else:
                end = line_end
            if end <= start:
                end = start+0.08
            text = _styled_line(line_words,local_i,rtl)
            events.append(
                f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Karaoke,,0,0,0,,{text}"
            )

    out_path.write_text(header+"\n".join(events)+"\n",encoding="utf-8-sig")
    return out_path

def render_karaoke(
    source_path: Path,
    instrumental_path: Path,
    ass_path: Path,
    output_path: Path,
    use_original_video: bool,
    resolution: str = "1920x1080",
) -> Path:
    ensure_ffmpeg()
    workdir = ass_path.parent
    local_ass = workdir/"karaoke.ass"
    if ass_path.resolve() != local_ass.resolve():
        shutil.copy2(ass_path,local_ass)

    vf = "subtitles=karaoke.ass"
    if use_original_video and source_path.suffix.lower() in VIDEO_EXTS:
        cmd = [
            "ffmpeg","-y",
            "-i",str(source_path),
            "-i",str(instrumental_path),
            "-map","0:v:0","-map","1:a:0",
            "-vf",vf,
            "-c:v","libx264","-preset","medium","-crf","18",
            "-c:a","aac","-b:a","320k",
            "-movflags","+faststart","-shortest",str(output_path)
        ]
    else:
        duration = probe_duration(instrumental_path)
        cmd = [
            "ffmpeg","-y",
            "-f","lavfi","-i",f"color=c=black:s={resolution}:r=30:d={duration:.3f}",
            "-i",str(instrumental_path),
            "-vf",vf,
            "-c:v","libx264","-preset","medium","-crf","18",
            "-c:a","aac","-b:a","320k",
            "-pix_fmt","yuv420p",
            "-movflags","+faststart","-shortest",str(output_path)
        ]
    run(cmd,cwd=workdir)
    return output_path

def build_karaoke(
    source_path: Path,
    lyrics_text: str,
    workdir: Path,
    language: str,
    separation_quality: str,
    whisper_model: str,
    use_original_video: bool,
    font_name: str,
    font_size: int,
    progress=None,
) -> dict:
    def notify(name: str,value: float):
        if progress:
            progress(name,value)

    ensure_ffmpeg()
    workdir.mkdir(parents=True,exist_ok=True)

    notify("Extracting audio",0.08)
    audio = extract_audio(source_path,workdir/"source.wav")

    notify("Separating vocals / instrumental",0.18)
    instrumental,vocals = separate_stems(audio,workdir/"stems",separation_quality)

    notify("Detecting word timings",0.50)
    asr_words,detected_language = transcribe_word_times(vocals,language,whisper_model)

    notify("Aligning your lyrics to the singing",0.72)
    lyrics,lines = parse_lyrics(lyrics_text)
    mapping = sequence_align(lyrics,asr_words)
    duration = probe_duration(instrumental)
    apply_and_interpolate_timings(lyrics,asr_words,mapping,duration)

    notify("Building karaoke subtitles",0.82)
    ass = create_ass(
        lyrics,lines,workdir/"karaoke.ass",
        font_name=font_name,font_size=font_size
    )

    notify("Rendering final MP4",0.90)
    output = render_karaoke(
        source_path,instrumental,ass,workdir/"karaoke.mp4",use_original_video
    )

    notify("Done",1.0)
    return {
        "video":output,
        "instrumental":instrumental,
        "subtitles":ass,
        "language":detected_language,
        "matched_words":len(mapping),
        "total_lyric_words":len(lyrics),
    }
