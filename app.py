from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import streamlit as st

from engine import KaraokeError, SEPARATION_MODELS, build_karaoke, ensure_ffmpeg

st.set_page_config(page_title="Karaoke Maker HE/EN", page_icon="🎤", layout="wide")

st.title("🎤 Karaoke Maker — Hebrew + English")
st.caption("Instrumental track + karaoke MP4 with word-by-word highlighting.")

st.info(
    "Use only audio/video and lyrics that you own, are licensed to use, "
    "or are in the public domain. This app does not download media from YouTube."
)

with st.sidebar:
    st.header("Settings")

    language_label = st.selectbox(
        "Song language", ["Auto detect", "Hebrew", "English"], index=0
    )
    language = {"Auto detect":"auto","Hebrew":"he","English":"en"}[language_label]

    separation_quality = st.selectbox(
        "Vocal removal", list(SEPARATION_MODELS.keys()), index=0
    )

    whisper_model = st.selectbox(
        "Timing model", ["small","medium","large-v3"], index=1,
        help="large-v3 is best with an NVIDIA GPU; medium is a good default."
    )

    font_name = st.text_input("Subtitle font", value="Arial")
    font_size = st.slider("Subtitle size", 36, 86, 58, 2)

    use_original_video = st.checkbox(
        "Use uploaded video as background", value=True
    )

youtube_ref = st.text_input(
    "YouTube URL (optional reference only)",
    placeholder="https://www.youtube.com/watch?v=..."
)
if youtube_ref:
    st.caption("The URL is not downloaded. Upload the authorized media file below.")

uploaded = st.file_uploader(
    "Upload an audio or video file",
    type=["mp3","wav","flac","m4a","aac","ogg","opus",
          "mp4","mkv","mov","avi","webm","m4v"]
)

lyrics = st.text_area(
    "Paste the lyrics you are authorized to use",
    height=280,
    placeholder="Keep natural line breaks.\nעברית ואנגלית נתמכות."
)

c1,c2 = st.columns([1,2])
with c1:
    create = st.button("Create karaoke", type="primary", use_container_width=True)
with c2:
    st.caption(
        "First run downloads AI models. CPU works, but NVIDIA GPU is much faster."
    )

if create:
    if not uploaded:
        st.error("Please upload an audio or video file.")
        st.stop()
    if not lyrics.strip():
        st.error("Please paste the lyrics.")
        st.stop()

    try:
        ensure_ffmpeg()
    except KaraokeError as exc:
        st.error(str(exc))
        st.stop()

    file_bytes = uploaded.getvalue()
    session_hash = hashlib.sha256(
        file_bytes + lyrics.encode("utf-8")
    ).hexdigest()[:16]

    base = Path(tempfile.gettempdir())/"karaoke_maker"/session_hash
    base.mkdir(parents=True, exist_ok=True)

    source = base/uploaded.name
    source.write_bytes(file_bytes)

    bar = st.progress(0.0)
    status = st.empty()

    def update(name: str, value: float):
        status.write(f"**{name}…**")
        bar.progress(min(max(value,0.0),1.0))

    try:
        result = build_karaoke(
            source_path=source,
            lyrics_text=lyrics,
            workdir=base,
            language=language,
            separation_quality=separation_quality,
            whisper_model=whisper_model,
            use_original_video=use_original_video,
            font_name=font_name,
            font_size=font_size,
            progress=update,
        )
    except KaraokeError as exc:
        st.error(str(exc))
        st.stop()
    except Exception as exc:
        st.exception(exc)
        st.stop()

    status.success(
        f"Done. Language: {result['language']} · "
        f"matched {result['matched_words']}/{result['total_lyric_words']} lyric words."
    )
    bar.progress(1.0)

    video_path = Path(result["video"])
    instrumental_path = Path(result["instrumental"])
    ass_path = Path(result["subtitles"])

    st.video(video_path.read_bytes())

    d1,d2,d3 = st.columns(3)
    with d1:
        st.download_button(
            "Download karaoke MP4",
            data=video_path.read_bytes(),
            file_name="karaoke.mp4",
            mime="video/mp4",
            use_container_width=True,
        )
    with d2:
        st.download_button(
            "Download instrumental WAV",
            data=instrumental_path.read_bytes(),
            file_name="instrumental.wav",
            mime="audio/wav",
            use_container_width=True,
        )
    with d3:
        st.download_button(
            "Download ASS subtitles",
            data=ass_path.read_bytes(),
            file_name="karaoke.ass",
            mime="text/plain",
            use_container_width=True,
        )
