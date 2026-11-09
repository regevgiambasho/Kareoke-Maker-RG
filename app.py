from __future__ import annotations

import json
import math
import os
import time
from dataclasses import asdict
from pathlib import Path

import pandas as pd
import streamlit as st

from chunk_uploader import chunk_uploader

from engine import (
    BuildConfig,
    KaraokeError,
    STYLE_PRESETS,
    build_karaoke,
    finalize_reviewed_project,
    create_project_from_file,
    load_editable_lines,
    load_manual_lyrics_text,
    save_corrected_lines,
    save_manual_lyrics_text,
)
from project_store import delete_project, get_project, list_projects
from upload_routes import resolve_completed_upload
from version import APP_NAME, APP_VERSION


st.set_page_config(
    page_title="Karaoke Maker V2",
    page_icon="🎤",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
<style>
:root { --accent:#8b5cf6; --panel:#15151f; --muted:#a1a1aa; }
.stApp {
  background:
    radial-gradient(circle at 20% 0%, rgba(139,92,246,.17), transparent 28rem),
    radial-gradient(circle at 90% 20%, rgba(236,72,153,.12), transparent 24rem),
    #09090f;
}
.block-container { max-width: 1080px; padding-top: 1.3rem; padding-bottom: 4rem; }
.hero {
  padding: 1.8rem 0 1.1rem 0;
}
.hero h1 {
  font-size: clamp(2.2rem, 6vw, 4.6rem);
  line-height: .96; margin: 0 0 .65rem 0;
  letter-spacing: -.045em;
}
.hero p { color:#b8b8c5; font-size:1.08rem; max-width:700px; }
.card {
  border:1px solid rgba(255,255,255,.09);
  border-radius:22px;
  background:rgba(21,21,31,.78);
  padding:1.05rem 1.15rem;
  box-shadow:0 14px 40px rgba(0,0,0,.25);
}
.kicker { color:#a78bfa; font-weight:700; letter-spacing:.08em; font-size:.78rem; }
.stagebar {
  color:#d4d4dc; font-size:.93rem;
  padding:.65rem .9rem; border-radius:12px; background:rgba(255,255,255,.04);
}
.eq { display:flex; gap:4px; align-items:flex-end; height:26px; margin:6px 0 3px; }
.eq i { display:block; width:4px; height:8px; border-radius:3px; background:#a78bfa; animation:eq 1.1s ease-in-out infinite; }
.eq i:nth-child(2){animation-delay:.12s}.eq i:nth-child(3){animation-delay:.24s}.eq i:nth-child(4){animation-delay:.36s}.eq i:nth-child(5){animation-delay:.48s}
@keyframes eq {0%,100%{height:7px;opacity:.55}50%{height:24px;opacity:1}}
div[data-testid="stFileUploader"] {
  border:1px dashed rgba(167,139,250,.55);
  padding:14px; border-radius:20px; background:rgba(139,92,246,.055);
}
div.stButton > button, div.stDownloadButton > button { border-radius:14px; min-height:46px; font-weight:700; }
.smallmuted {color:#9191a0;font-size:.87rem}
</style>
""",
    unsafe_allow_html=True,
)

if "current_project_id" not in st.session_state:
    st.session_state.current_project_id = None
if "show_video" not in st.session_state:
    st.session_state.show_video = False
if "open_editor" not in st.session_state:
    st.session_state.open_editor = False
if "uploader_nonce" not in st.session_state:
    st.session_state.uploader_nonce = 0


def start_new_project() -> None:
    """
    Reset the current workspace without deleting any saved project/history.
    Incrementing uploader_nonce forces Streamlit to create a fresh uploader.
    """
    st.session_state.current_project_id = None
    st.session_state.show_video = False
    st.session_state.open_editor = False
    st.session_state.uploader_nonce += 1

    # Clear any optional transient result/editor flags we may add later.
    for key in list(st.session_state.keys()):
        if key.startswith("temp_") or key.startswith("preview_"):
            del st.session_state[key]


def fmt_elapsed(seconds: float) -> str:
    seconds = int(max(0, seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def result_from_meta(meta: dict | None):
    if not meta or meta.get("status") != "done":
        return None
    outputs = meta.get("outputs") or {}
    needed = ("video", "instrumental", "subtitles")
    if not all(outputs.get(x) and Path(outputs[x]).exists() for x in needed):
        return None
    return meta


def show_result(meta: dict, scope: str = "create") -> None:
    outputs = meta["outputs"]
    video = Path(outputs["video"])
    instrumental = Path(outputs["instrumental"])
    subtitles = Path(outputs["subtitles"])

    st.markdown("### ✅ Karaoke ready")
    actual_filename = (
        meta.get("output_filename")
        or Path((meta.get("outputs") or {}).get("video", "")).name
    )
    if actual_filename:
        st.caption(f"Final file: {actual_filename}")
    verified = bool(meta.get("lyrics_verified"))
    source = meta.get("lyrics_source") or "whisper"
    confidence = float(meta.get("lyrics_confidence") or 0.0)
    if verified:
        st.success(f"Lyrics verified · source: {source} · alignment confidence: {confidence:.0%}")
    else:
        st.info("Lyrics source: Whisper fallback · review the Lyrics editor before final export if needed.")
    c1, c2, c3 = st.columns([1.1, 1.1, .8])

    with c1:
        if st.button(
            "▶ View Karaoke",
            use_container_width=True,
            type="primary",
            key=f"{scope}_{meta['project_id']}_view",
        ):
            st.session_state.show_video = not st.session_state.show_video

    with c2:
        st.link_button(
            "⬇ Download Karaoke",
            url=f"/api/download/{meta['project_id']}/video",
            type="primary",
            use_container_width=True,
            help="Direct resumable download",
        )

    with c3:
        if st.button(
            "✏️ Edit lyrics",
            use_container_width=True,
            key=f"{scope}_{meta['project_id']}_edit_lyrics",
        ):
            st.session_state["open_editor"] = True
            st.rerun()

    if st.session_state.show_video:
        st.video(str(video))

    if st.button(
        "➕ Start another project",
        use_container_width=True,
        key=f"{scope}_{meta['project_id']}_new_project",
    ):
        start_new_project()
        st.rerun()

    more_label = "More downloads" if scope == "create" else "More downloads · Projects"
    with st.expander(more_label):
        c1, c2 = st.columns(2)
        with c1:
            st.link_button(
                "Instrumental WAV",
                url=f"/api/download/{meta['project_id']}/instrumental",
                use_container_width=True,
                help="Direct resumable download",
            )
        with c2:
            st.link_button(
                "ASS subtitles",
                url=f"/api/download/{meta['project_id']}/subtitles",
                use_container_width=True,
                help="Direct download",
            )




def request_manual_lyrics(meta: dict, scope: str = "create") -> None:
    project_dir = Path(meta["project_dir"])
    review = meta.get("review") or {}
    lookup = review.get("lyrics_lookup") or {}

    st.markdown("### 📝 Lyrics needed")

    recognized = " — ".join(
        x for x in [meta.get("song_title"), meta.get("artist_name")] if x
    )
    if recognized:
        st.caption(f"Song: {recognized}")

    st.info(
        "We couldn't find reliable lyrics automatically. "
        "Paste the correct lyrics below to continue."
    )

    lyrics_text = st.text_area(
        "Paste lyrics",
        value="",
        height=360,
        placeholder="Paste the full lyrics here, one lyric line per row…",
        key=f"manual_needed_{scope}_{meta['project_id']}",
    )

    if st.button(
        "CREATE KARAOKE WITH THESE LYRICS",
        type="primary",
        use_container_width=True,
        key=f"manual_continue_{scope}_{meta['project_id']}",
    ):
        if not lyrics_text.strip():
            st.error("Please paste the lyrics first.")
            return

        source = Path(meta["source_path"])
        config_data = dict(meta["config"])
        config_data["manual_lyrics_text"] = lyrics_text.strip()
        config_data["require_verified_lyrics"] = True
        config = BuildConfig(**config_data)

        bar = st.progress(0)
        status = st.empty()
        started = time.time()

        def cb(pct: float, message: str, stage: str):
            bar.progress(int(max(0, min(100, pct * 100))))
            status.markdown(
                f"**{stage}** · {message} · {int(pct*100)}% · "
                f"elapsed {fmt_elapsed(time.time() - started)}"
            )

        try:
            result = build_karaoke(
                source,
                project_dir,
                config,
                cb,
                stop_before_render=False,
            )
            st.session_state.current_project_id = result.project_id
            st.session_state.show_video = True
            st.rerun()
        except Exception as exc:
            st.error("Could not continue processing.")
            with st.expander("Show technical details"):
                st.code(str(exc))



def review_before_render(meta: dict, scope: str = "create") -> None:
    project_dir = Path(meta["project_dir"])
    review = meta.get("review") or {}
    outputs = meta.get("outputs") or {}

    lines_path = outputs.get("lines")
    if not lines_path or not Path(lines_path).exists():
        st.error("Review data is missing.")
        return

    lines = json.loads(Path(lines_path).read_text(encoding="utf-8"))
    default_text = "\n".join(
        str(line.get("text", "")).strip()
        for line in lines
        if str(line.get("text", "")).strip()
    )

    source_name = meta.get("lyrics_source") or "whisper"
    verified = bool(meta.get("lyrics_verified"))
    confidence = float(meta.get("lyrics_confidence") or 0.0)

    st.markdown("### 📝 Review Lyrics Before Render")

    lookup = review.get("lyrics_lookup") or {}
    recognized_title = meta.get("song_title")
    recognized_artist = meta.get("artist_name")

    if recognized_title or recognized_artist:
        identity_source = review.get("identity_source") or "unknown"
        st.caption("Identified song: " + " — ".join(x for x in [recognized_title, recognized_artist] if x) + f" · source: {identity_source}")

    if verified:
        source_label = "Manual lyrics" if source_name == "manual" else source_name
        st.success(
            f"Lyrics ready · source: {source_label} · "
            f"sync confidence {confidence:.0%}"
        )

    edited_text = st.text_area(
        "Lyrics — edit before final render",
        value=default_text,
        height=420,
        key=f"review_text_{scope}_{meta['project_id']}",
        help="One karaoke line per row. This text becomes authoritative.",
    )

    c1, c2 = st.columns(2)

    source_rotation = int(review.get("source_rotation") or 0)
    rotation_options = {
        "Auto": None,
        "0°": 0,
        "90°": 90,
        "180°": 180,
        "270°": 270,
    }

    with c1:
        default_rotation_label = (
            f"Auto"
            if source_rotation == 0
            else "Auto"
        )
        rotation_label = st.selectbox(
            "Video orientation",
            options=list(rotation_options.keys()),
            index=0,
            key=f"rotation_{scope}_{meta['project_id']}",
            help=f"Detected source rotation: {source_rotation}°",
        )

    suggested = float(review.get("suggested_sync_offset") or 0.0)
    with c2:
        sync_offset = st.number_input(
            "Lyrics sync offset (seconds)",
            min_value=-15.0,
            max_value=15.0,
            value=float(suggested),
            step=0.10,
            format="%.2f",
            key=f"sync_{scope}_{meta['project_id']}",
            help=(
                "Negative = lyrics earlier. Positive = lyrics later. "
                f"Suggested calibration: {suggested:+.2f}s"
            ),
        )

    st.caption(
        f"Detected rotation: {source_rotation}° · "
        f"Suggested initial sync correction: {suggested:+.2f}s"
    )

    render_button_label = "✅ Approve Lyrics & Render"

    if st.button(
        render_button_label,
        type="primary",
        use_container_width=True,
        key=f"approve_render_{scope}_{meta['project_id']}",
    ):
        if not edited_text.strip():
            st.error("Lyrics cannot be empty.")
            return

        source = Path(meta["source_path"])
        config_data = dict(meta["config"])
        config_data["manual_lyrics_text"] = None
        config_data["rotation_override"] = rotation_options[rotation_label]
        config = BuildConfig(**config_data)

        bar = st.progress(0)
        status = st.empty()
        started = time.time()

        def cb(pct: float, message: str, stage: str):
            bar.progress(int(max(0, min(100, pct * 100))))
            status.markdown(
                f"**{stage}** · {message} · {int(pct*100)}% · "
                f"elapsed {fmt_elapsed(time.time() - started)}"
            )

        try:
            finalize_reviewed_project(
                project_dir=project_dir,
                source_path=source,
                config=config,
                approved_lyrics_text=edited_text,
                sync_offset_seconds=float(sync_offset),
                rotation_override=rotation_options[rotation_label],
                progress=cb,
            )
            st.session_state.current_project_id = meta["project_id"]
            st.session_state.show_video = True
            st.success("Reviewed karaoke rendered successfully.")
            st.rerun()
        except Exception as exc:
            st.error("Final render failed.")
            with st.expander("Show technical details"):
                st.code(str(exc))


def lyrics_editor(meta: dict, scope: str = "create") -> None:
    project_dir = Path(meta["project_dir"])
    rows = load_editable_lines(project_dir)
    current_text = load_manual_lyrics_text(project_dir)

    if not rows and not current_text:
        st.warning("No lyric data is available for this project yet.")
        return

    st.markdown("### ✏️ Lyrics editor")
    st.caption(
        "Quick edit changes the lyric text while reusing the existing audio, stems and "
        "transcription. Only subtitles and the final video are rebuilt."
    )

    quick_tab, timing_tab = st.tabs(["Quick text edit", "Advanced timing"])

    with quick_tab:
        edited_text = st.text_area(
            "Lyrics — one karaoke line per row",
            value=current_text,
            height=360,
            key=f"lyrics_text_{scope}_{meta['project_id']}",
            help=(
                "Replace the text with the correct lyrics. The app will align your text "
                "to the existing timestamps. If alignment is weak, your text is preserved "
                "and timing is distributed across the song."
            ),
        )

        if st.button(
            "💾 Save lyrics & rerender",
            type="primary",
            use_container_width=True,
            key=f"save_text_lyrics_{scope}_{meta['project_id']}",
        ):
            if not edited_text.strip():
                st.error("Lyrics cannot be empty.")
            else:
                language = meta.get("detected_language") or "auto"
                try:
                    _, confidence = save_manual_lyrics_text(
                        project_dir,
                        edited_text,
                        language=language,
                    )

                    source = Path(meta["source_path"])
                    config_data = dict(meta["config"])
                    # Do not keep stale manual text in config; corrected_lines.json is authoritative.
                    config_data["manual_lyrics_text"] = None
                    config = BuildConfig(**config_data)

                    bar = st.progress(0)
                    status = st.empty()
                    started = time.time()

                    def cb(pct: float, message: str, stage: str):
                        bar.progress(int(pct * 100))
                        status.markdown(
                            f"**{stage}** · {message} · {int(pct*100)}% · "
                            f"{fmt_elapsed(time.time()-started)}"
                        )

                    build_karaoke(source, project_dir, config, cb)
                    st.success(
                        "Manual lyrics saved. Karaoke rerendered"
                        + (
                            f" · alignment confidence {confidence:.0%}"
                            if confidence > 0
                            else " · fallback timing used"
                        )
                    )
                    st.session_state.show_video = True
                    st.session_state.open_editor = False
                    st.rerun()
                except Exception as exc:
                    st.error("Could not rerender with the edited lyrics.")
                    with st.expander("Show technical details"):
                        st.code(str(exc))

    with timing_tab:
        if not rows:
            st.info("Timing rows will appear after lyric lines exist.")
            return

        st.caption(
            "Advanced mode lets you change individual line text and start/end times."
        )
        df = pd.DataFrame(rows)
        edited = st.data_editor(
            df,
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic",
            column_config={
                "start": st.column_config.NumberColumn(
                    "Start (s)", min_value=0.0, step=0.1
                ),
                "end": st.column_config.NumberColumn(
                    "End (s)", min_value=0.0, step=0.1
                ),
                "text": st.column_config.TextColumn("Lyrics", width="large"),
            },
            key=f"lyrics_editor_{scope}_{meta['project_id']}",
        )

        if st.button(
            "💾 Save timing corrections & rerender",
            type="secondary",
            use_container_width=True,
            key=f"save_timing_lyrics_{scope}_{meta['project_id']}",
        ):
            records = edited.to_dict("records")
            save_corrected_lines(project_dir, records)

            source = Path(meta["source_path"])
            config_data = dict(meta["config"])
            config_data["manual_lyrics_text"] = None
            config = BuildConfig(**config_data)

            bar = st.progress(0)
            status = st.empty()
            started = time.time()

            def cb(pct: float, message: str, stage: str):
                bar.progress(int(pct * 100))
                status.markdown(
                    f"**{stage}** · {message} · {int(pct*100)}% · "
                    f"{fmt_elapsed(time.time()-started)}"
                )

            try:
                build_karaoke(source, project_dir, config, cb)
                st.success("Timing corrections saved. Karaoke rerendered.")
                st.session_state.show_video = True
                st.session_state.open_editor = False
                st.rerun()
            except Exception as exc:
                st.error("Rerender failed.")
                with st.expander("Show technical details"):
                    st.code(str(exc))

def resume_failed_project(meta: dict, scope: str = "create") -> None:
    source = Path(meta["source_path"])
    project_dir = Path(meta["project_dir"])
    config = BuildConfig(**meta["config"])

    st.markdown("### ↻ Resume project")
    st.caption(
        "Completed stages are kept. Resume starts from the first missing or failed stage."
    )

    if st.button(
        "↻ Retry from last completed step",
        type="primary",
        use_container_width=True,
        key=f"resume_{scope}_{meta['project_id']}",
    ):
        bar = st.progress(0)
        status = st.empty()
        started = time.time()
        state = {"last": 0.0}

        def cb(pct: float, message: str, stage: str):
            pct = max(state["last"], pct)
            state["last"] = pct
            bar.progress(int(pct * 100))
            elapsed = time.time() - started
            eta = None
            if pct >= 0.08 and pct < 0.995:
                eta = elapsed * (1 - pct) / max(pct, 0.01)
            status.markdown(
                f"**{stage}** · {message} · {int(pct*100)}% · "
                f"elapsed {fmt_elapsed(elapsed)}"
                + (f" · ETA {fmt_elapsed(eta)}" if eta is not None else "")
            )

        try:
            result = build_karaoke(source, project_dir, config, cb)
            st.session_state.current_project_id = result.project_id
            st.session_state.show_video = False
            st.success("Project resumed successfully.")
            st.rerun()
        except Exception as exc:
            st.error("Resume failed. Completed stages are still saved.")
            with st.expander("Show technical details"):
                st.code(str(exc))


st.markdown(
    """
<div class="hero">
  <div class="kicker">KARAOKE MAKER · V2.0</div>
  <h1>Turn any song<br>into karaoke.</h1>
  <p>Find reliable lyrics, separate vocals, sync precisely and render a ready-to-play karaoke video.</p>
</div>
""",
    unsafe_allow_html=True,
)

new_project_col, _ = st.columns([1, 3])
with new_project_col:
    if st.button(
        "➕ Start New Project",
        use_container_width=True,
        key="start_new_project_top",
    ):
        start_new_project()
        st.rerun()

tab_create, tab_history = st.tabs(["✨ Create", "🕘 Projects"])

with tab_create:
    st.markdown("#### 1 · Upload")

    uploaded_info = chunk_uploader(
        key=f"media_uploader_{st.session_state.uploader_nonce}",
        chunk_size=4 * 1024 * 1024,
    )

    server_input_path = None
    if uploaded_info and uploaded_info.get("upload_id"):
        server_input_path = resolve_completed_upload(uploaded_info["upload_id"])
        if server_input_path is None:
            st.warning("The uploaded file is no longer available. Please upload it again.")

    st.markdown("#### 2 · Performance")
    performance_mode = st.segmented_control(
        "Performance mode",
        options=["Fast", "Balanced", "Best quality"],
        default="Fast",
        selection_mode="single",
        label_visibility="collapsed",
    )
    perf_desc = {
        "Fast": "⚡ Fast · fast separation + ASR timing anchors; reliable lyrics stay authoritative.",
        "Balanced": "⚖️ Balanced · recommended quality/speed mix.",
        "Best quality": "🎯 Best quality · slower, stronger transcription and full alignment.",
    }
    st.caption(perf_desc[performance_mode])

    st.markdown("#### 3 · Karaoke style")
    style_preset = st.segmented_control(
        "Style",
        options=list(STYLE_PRESETS),
        default="Classic",
        selection_mode="single",
        label_visibility="collapsed",
    )

    with st.expander("Advanced settings"):
        language_label = st.selectbox(
            "Language",
            ["Auto detect", "Hebrew", "English"],
            index=0,
        )
        language_map = {"Auto detect": "auto", "Hebrew": "he", "English": "en"}
        use_verified_lyrics = st.checkbox(
            "Automatic lyrics lookup",
            value=True,
            help="Hebrew prefers Musixmatch when configured, then LRCLIB fallback. Whisper is only a draft fallback.",
        )

        require_verified_lyrics = True

        manual_lyrics_text = None
        if use_verified_lyrics:
            musix_connected = bool(os.environ.get("MUSIXMATCH_API_KEY", "").strip())
            audd_connected = bool(os.environ.get("AUDD_API_TOKEN", "").strip())
            custom_connected = bool(os.environ.get("LICENSED_LYRICS_API_URL", "").strip())
            provider_labels = []
            if musix_connected:
                provider_labels.append("Musixmatch")
            if custom_connected:
                provider_labels.append("Custom licensed API")
            provider_labels.extend(["lyrics.ovh", "LRCLIB fallback"])
            if audd_connected:
                provider_labels.insert(0, "AudD recognition")

            st.caption("Automatic pipeline: " + " → ".join(provider_labels))

            if language_label == "Hebrew" and not musix_connected:
                st.info(
                    "Hebrew free mode: lyrics.ovh is tried first, then LRCLIB. "
                    "Musixmatch can be added later for broader licensed coverage."
                )
            elif musix_connected:
                st.success(
                    "Musixmatch connected. Hebrew lyrics will prefer Musixmatch, "
                    "then lyrics.ovh and LRCLIB as fallbacks."
                )
            if not audd_connected:
                st.caption("AudD is not configured. Song identity will come from manual fields, a Musixmatch URL, embedded metadata, or the filename.")

        song_title = st.text_input(
            "Song title (optional)",
            placeholder="Auto-detect from media metadata when possible",
        )
        artist_name = st.text_input(
            "Artist (optional)",
            placeholder="Auto-detect from media metadata when possible",
        )
        musixmatch_url_hint = st.text_input(
            "Musixmatch song URL (optional)",
            placeholder="https://www.musixmatch.com/lyrics/Artist/Song",
            help=(
                "Optional identity hint only. V2 does not scrape lyrics from the page; "
                "it uses the official Musixmatch API for lyrics."
            ),
        )
        audd_available = bool(os.environ.get("AUDD_API_TOKEN", "").strip())
        use_song_recognition = st.checkbox(
            "Automatically identify / validate song and artist with AudD",
            value=audd_available,
            disabled=not audd_available,
            help="AudD can replace weak filename/metadata guesses. Explicit Song title / Artist fields still take priority.",
        )
        if not audd_available:
            st.caption("ℹ️ AudD is not configured yet. Enter Song title / Artist manually, or connect AudD later.")
        use_custom_filename = st.checkbox(
            "Use a custom output filename",
            value=False,
            help="If unchecked, the filename will use: Song Title - Performing Artist - קריוקי.mp4",
        )
        custom_output_filename = None
        if use_custom_filename:
            custom_output_filename = st.text_input(
                "Custom output filename",
                placeholder="Example: המסיבה של ארבל - קריוקי",
                help="You may type the name with or without .mp4",
            )
        use_original_video = st.checkbox(
            "Use original video as background when available",
            value=True,
        )
        font_size = st.slider("Subtitle size", 34, 72, 48, 2)
        whisper_override = "Use performance preset"

    rights = st.checkbox(
        "I confirm that I am allowed to process this media.",
        value=False,
    )

    config = BuildConfig(
        performance_mode=performance_mode,
        language=language_map[language_label],
        use_original_video=use_original_video,
        style_preset=style_preset,
        font_size=font_size,
        whisper_model_override=None
        if whisper_override == "Use performance preset"
        else whisper_override,
        use_verified_lyrics=use_verified_lyrics,
        require_verified_lyrics=require_verified_lyrics,
        manual_lyrics_text=None,
        use_song_recognition=use_song_recognition,
        song_title=song_title.strip() or None,
        artist_name=artist_name.strip() or None,
        musixmatch_url_hint=musixmatch_url_hint.strip() or None,
        custom_output_filename=(
            custom_output_filename.strip()
            if use_custom_filename and custom_output_filename and custom_output_filename.strip()
            else None
        ),
    )

    input_ready = server_input_path is not None

    create = st.button(
        "🎤 CREATE KARAOKE",
        type="primary",
        use_container_width=True,
        disabled=not input_ready or not rights,
    )

    if create and input_ready:
        project_id, project_dir, source = create_project_from_file(
            server_input_path, config
        )
        st.session_state.current_project_id = project_id
        st.session_state.show_video = False

        progress_bar = st.progress(0)
        status_box = st.empty()
        stage_box = st.empty()
        metric_box = st.empty()
        started = time.time()
        progress_state = {"last_pct": 0.0}

        stage_names = ["Upload", "Extract", "Identify song", "Find lyrics", "Separate", "Sync", "Subtitles", "Render", "Done"]

        def progress_cb(pct: float, message: str, stage: str):
            pct = max(progress_state["last_pct"], pct)
            progress_state["last_pct"] = pct
            progress_bar.progress(int(pct * 100))

            elapsed = time.time() - started
            eta = None
            if pct >= 0.08 and pct < 0.995:
                eta = elapsed * (1 - pct) / max(pct, 0.01)

            status_box.markdown(
                f"""
<div class="card">
  <div class="eq"><i></i><i></i><i></i><i></i><i></i></div>
  <b>{message}</b><br>
  <span class="smallmuted">{int(pct*100)}% · elapsed {fmt_elapsed(elapsed)}
  {" · ETA " + fmt_elapsed(eta) if eta is not None else ""}</span>
</div>
""",
                unsafe_allow_html=True,
            )

            if stage in stage_names:
                idx = stage_names.index(stage)
            else:
                idx = 0
            parts = []
            for i, name in enumerate(stage_names):
                icon = "✓" if i < idx else ("●" if i == idx else "○")
                parts.append(f"{icon} {name}")
            stage_box.markdown(
                '<div class="stagebar">' + " &nbsp;→&nbsp; ".join(parts) + "</div>",
                unsafe_allow_html=True,
            )

        try:
            result = build_karaoke(
                source, project_dir, config, progress_cb, stop_before_render=False
            )
            meta = get_project(project_id)
            if result.resumed_stages:
                st.caption("Reused: " + ", ".join(result.resumed_stages))
            if meta:
                if meta.get("status") == "awaiting_lyrics_input":
                    st.session_state.current_project_id = project_id
                    st.rerun()
                elif meta.get("status") == "awaiting_review":
                    # Legacy project created by an older version.
                    st.session_state.current_project_id = project_id
                    st.rerun()
                else:
                    show_result(meta, scope="create_after_build")
        except Exception as exc:
            st.error(
                "Processing stopped. Your completed stages were saved, so Retry can resume instead of starting over."
            )
            with st.expander("Show technical details"):
                st.code(str(exc))


    current = (
        get_project(st.session_state.current_project_id)
        if st.session_state.current_project_id
        else None
    )
    if current and current.get("status") == "awaiting_lyrics_input":
        request_manual_lyrics(current, scope="create")
    elif current and current.get("status") == "awaiting_review":
        review_before_render(current, scope="create")
    elif not create and result_from_meta(current):
        show_result(current, scope="create")

    if current and current.get("status") == "failed":
        resume_failed_project(current, scope="create")

    if current and (
        st.session_state.get("open_editor")
        or result_from_meta(current)
    ):
        with st.expander("Lyrics editor · Current", expanded=bool(st.session_state.get("open_editor"))):
            lyrics_editor(current, scope="create")

with tab_history:
    projects = list_projects()

    if not projects:
        st.info("No projects yet.")
    else:
        options = {
            f"{p.get('title','Untitled')} · {p.get('status','unknown')} · {p.get('updated_at','')[:16]}": p["project_id"]
            for p in projects
        }
        label = st.selectbox("Project history", list(options.keys()))
        selected = get_project(options[label])

        if selected:
            c1, c2, c3 = st.columns([2, 1, 1])
            with c1:
                st.markdown(f"### {selected.get('title','Untitled')}")
                st.caption(
                    f"Status: {selected.get('status','unknown')} · "
                    f"Language: {selected.get('detected_language','—')}"
                )
            with c2:
                if st.button("Open project", use_container_width=True):
                    st.session_state.current_project_id = selected["project_id"]
                    st.session_state.show_video = False
                    st.rerun()
            with c3:
                if st.button("Delete", use_container_width=True):
                    delete_project(selected["project_id"])
                    if st.session_state.current_project_id == selected["project_id"]:
                        st.session_state.current_project_id = None
                    st.rerun()

            ready = result_from_meta(selected)
            if selected.get("status") == "awaiting_lyrics_input":
                request_manual_lyrics(selected, scope="history")
            elif selected.get("status") == "awaiting_review":
                review_before_render(selected, scope="history")
            elif ready:
                show_result(ready, scope="history")
                with st.expander("Lyrics editor · Projects"):
                    lyrics_editor(selected, scope="history")
            elif selected.get("status") == "failed":
                st.warning(
                    "This project stopped during processing. Resume will reuse completed stages."
                )
                resume_failed_project(selected, scope="history")
                if selected.get("error"):
                    with st.expander("Last error"):
                        st.code(selected["error"])
