# Karaoke Maker V2

Standalone V2 project for creating karaoke videos from media you are allowed to process.

## alpha4.9.3 — Video Fidelity & Timing

- Active-word highlight now leads canonical word timing by **280 ms**. Canonical sync timestamps remain untouched.
- Final video encoding now uses **H.264 CRF 14**, **preset medium**, and **tune film** in every performance mode.
- Original-video renders preserve the source frame rate and pixel dimensions; no scaling or interpolation is introduced by the karaoke renderer.
- Renderer signatures are bumped, so upgrading an existing project rebuilds only subtitles and the final MP4 while reusing stems and Anchor Sync.
- Existing countdown and next-line preview behavior from alpha4.9.2 is unchanged.

## alpha4.9.1 — Singer Guidance & Startup Stability

Built on alpha4.9 Anchor Sync. This release keeps canonical timing data intact and improves the karaoke presentation layer:

- active-word highlight is visually advanced by **120 ms**;
- the **next lyric line** appears smaller beneath the active line during normal short transitions;
- instrumental gaps of **4 seconds or more** use a **3 → 2 → 1** countdown to the true next vocal onset;
- long-gap countdowns suppress the next-line preview so the screen stays uncluttered;
- subtitle placement is raised to make room for the preview line;
- the final FFmpeg render rebases video/audio PTS to zero and applies a 30 ms audio fade-in to reduce startup glitches.


## alpha4.9.2 — Guidance cache fix

- Renderer/video stages now carry versioned signatures. Opening an older project after a renderer upgrade automatically rebuilds only subtitles and final video; stems and Precision Sync are reused.
- Active-word highlight leads canonical word timing by 200 ms.
- The next lyric line is shown smaller below the active line whenever vocals continue normally.
- A visible 3-2-1 countdown appears before the first vocal entry and after instrumental gaps of 3 seconds or longer.
- Countdown onset is protected against stale zero-valued word timestamps.
- The 30 ms startup audio fade from alpha4.9.1 was removed; startup now uses timestamp reset plus audio resampling to first_pts=0.

## What changed in alpha.2

### Lyrics Verification & Alignment
- WhisperX is used primarily as a timing source.
- V2 can query a configured **licensed full-lyrics API** in the background.
- Canonical lyrics are sequence-aligned against Whisper word timestamps.
- Verified lyrics are accepted only when alignment confidence passes the threshold.
- Provider line breaks are preserved instead of mechanically grouping every few words.
- If lookup is unavailable or confidence is weak, V2 safely falls back to Whisper.

### Hebrew / RTL renderer
V1 placed many ASS karaoke tags inside one RTL sentence. In V2:
- each word is positioned independently;
- Hebrew word 1 is placed on the right, then subsequent words move left;
- the full line remains visible;
- only the active word gets a timed highlight overlay;
- FFmpeg renders ASS using `shaping=complex`.

This avoids reversed Hebrew word order and left-to-right highlight progression.

## Other V2 features

- Fast / Balanced / Best quality modes
- Real-time stage progress where available
- Resume from completed stages after a failure
- Shared cache for stems and transcripts
- Lyrics correction editor
- Style presets: Classic, Neon, Minimal, Kids
- Project history
- View / Download result actions
- Dark, mobile-friendly UI
- Isolated WhisperX worker process
- Smart output filenames
- FFmpeg rendering progress

## Run in Google Colab (no local install)

Open [`colab/Karaoke_Maker_Colab.ipynb` in Colab](https://colab.research.google.com/github/regevgiambasho/Kareoke-Maker-RG/blob/main/colab/Karaoke_Maker_Colab.ipynb),
select **Runtime → Change runtime type → T4 GPU**, run step 1 (install) and step 2
(start), then click **Open Karaoke Maker**. Colab sessions are temporary: download
finished videos before closing the tab.

## Run in GitHub Codespaces

The repository includes a Python 3.11 devcontainer.

After the Codespace finishes building:

```bash
python launcher.py
```

Open port **8501** from the Codespaces **Ports** tab.

## Optional licensed lyrics provider

V2 deliberately does **not** scrape lyrics websites.

Configure a licensed provider/service with:

```bash
export LICENSED_LYRICS_API_URL="https://your-provider.example/api/lyrics"
export LICENSED_LYRICS_API_TOKEN="..."
```

See `V2_SPEC.md` for the request/response contract. The configured service may use title/artist or the short transcription hint to identify the song.

If no provider is configured, the app continues with Whisper fallback and clearly marks the result as unverified.

## Project files

```text
Karaoke-Maker-V2/
├── app.py
├── engine.py
├── lyrics_engine.py
├── transcribe_worker.py
├── project_store.py
├── version.py
├── requirements.txt
├── README.md
├── V2_SPEC.md
├── .env.example
├── .gitignore
├── .streamlit/
│   └── config.toml
└── .devcontainer/
    └── devcontainer.json
```

Runtime projects/cache are stored under `~/.karaoke_maker_v2/` rather than inside the Git repository.


## Musixmatch provider

V2 includes an optional Musixmatch provider using the official API.

In the Codespace terminal:

```bash
export MUSIXMATCH_API_KEY="your_api_key_here"
```

Restart Streamlit after setting the environment variable. If title and artist are
available, the app tries Musixmatch before the generic licensed-provider adapter.
If no usable full response is available, it falls back to Whisper instead of using
incomplete lyrics.


## Chunked browser upload

V2 uses one drag-and-drop uploader for both small and large media.

The browser splits the selected file into 4 MB chunks and sends them to
custom upload routes on the same Streamlit origin. This avoids large
single-request uploads through deployment proxies while keeping the user
experience identical for every file size.

Start the app with:

```bash
python launcher.py
```

`launcher.py` uses Streamlit's `st.App` API so the upload routes and the
Streamlit UI share port 8501.


## Trusted-first mode

For automatic accurate lyrics, configure both services:

```bash
export AUDD_API_TOKEN="..."
export MUSIXMATCH_API_KEY="..."
```

AudD identifies the track. Musixmatch is then queried using the identified
title and artist. If no usable trusted lyrics are returned, V2 stops at the
Review Lyrics screen instead of silently rendering guessed Whisper text.


## alpha4.2 Musixmatch integration

Musixmatch now uses its official `track.search` followed by
`track.lyrics.get` flow.

Optional identity hint:

```text
https://www.musixmatch.com/lyrics/Artist/Song
```

The public URL is parsed only for artist/title hints. Lyrics are retrieved
only through the configured official API.


## alpha4.3 Fast mode

Fast is the recommended default for interactive use. It combines a
karaoke-focused separator, batched INT8 faster-whisper word timestamps,
VAD, automatic CPU thread usage, an ultrafast FFmpeg preset, and shared
caching. Manual/trusted lyrics no longer trigger WhisperX forced alignment.


## alpha4.4 Precision Sync

Manual/trusted lyrics use exact-text CTC forced alignment against the vocal
stem in Fast mode. This avoids ASR-text mismatch and interpolation drift,
especially in rapid Hebrew vocals and rap.


## alpha4.5 Automatic lyrics

LRCLIB is now the first automatic lyrics source and requires no API key.
If title/artist metadata is missing, filenames such as
`Song - Artist.mp4` are used as a local identity hint. Exact lookup also
includes track duration for safer matching.

When LRCLIB returns a confident match, its canonical text is sent through
Precision Sync against the vocal stem. Whisper remains a last-resort draft.


## alpha4.6 Hebrew automatic pipeline

Hebrew tracks prefer Musixmatch when `MUSIXMATCH_API_KEY` is configured. LRCLIB remains a free fallback. AudD validates weak filename/metadata identity.


## alpha4.7 — lyrics.ovh free automatic lyrics

Automatic lyrics now include the free/keyless lyrics.ovh API.

Provider order for Hebrew:

`Musixmatch (if configured) → custom licensed API → lyrics.ovh → LRCLIB → Whisper draft`

Without paid providers:

`Identity → lyrics.ovh → LRCLIB → Review/Whisper draft`

lyrics.ovh is queried first by exact artist/title. If that misses, V2 uses the
lyrics.ovh suggestion endpoint to recover canonical spelling/punctuation and
tries the strongest few candidate identities.

No API key is required for lyrics.ovh.


## alpha4.8 — Simple Flow

The app no longer generates a Whisper lyric draft when automatic lyrics fail.

Pipeline:

`Upload → Extract → Identify → Find reliable lyrics`

If lyrics are found:
`Separate → Precision Sync → Review → Render`

If lyrics are not found:
`Paste lyrics → Separate → Precision Sync → Review → Render`

The app stops before expensive separation when reliable lyrics are unavailable.
This saves time and keeps the UX simple. Whisper is no longer used to invent
lyric text.


## alpha4.8.1 — Single lyrics input

The legacy upfront Manual Lyrics control was removed from Advanced Settings.
Manual lyrics are requested only when automatic reliable lookup fails.

The post-build transition now reruns the Streamlit page before showing either
`awaiting_lyrics_input` or `awaiting_review`, preventing duplicate boxes from
being rendered in the same UI pass.


## alpha4.8.2 — filename identity retry

Files named `X - Y` are ambiguous. Some libraries use `Song - Artist`;
others use `Artist - Song`.

If automatic lyrics lookup fails and identity came only from the filename,
V2 now retries the lookup with title/artist swapped. If the swapped lookup
succeeds, that identity becomes the resolved song identity.


## alpha4.8.3 — Chunked Precision Sync

Long songs are no longer sent to WhisperX forced alignment as one giant
full-song segment.

Lyrics are grouped into moderate chunks. Each chunk gets a rough search window
based on vocal-stem activity and its share of the lyric word count, then
WhisperX aligns only that short audio window.

Benefits:
- much lower peak memory on CPU/Codespaces;
- avoids full-song CTC alignment crashes;
- progress is visible per chunk;
- one difficult chunk no longer kills the whole job;
- exact user/provider lyric text remains authoritative.

If one individual chunk cannot align, only that chunk receives proportional
fallback timing.


## alpha4.8.4 — Auto Render

The extra pre-render approval step is removed from the normal workflow.

New flow:

`Lyrics ready → Separate → Precision Sync → Subtitles → Render → Done`

There is no second `Review Lyrics Before Render` stop, no orientation selector,
no sync-offset control, and no `Approve Lyrics & Render` button for new
projects.

Automatic rotation and synchronization remain internal. After rendering, the
existing Lyrics Editor is still available for corrections and rerendering.

Older saved projects with `awaiting_review` status remain readable for backward
compatibility.


## alpha4.8.5 — identity + overlap + timing stability

Fixes three issues:

1. A filename containing only a song title is now used as a title-only identity
   hint. lyrics.ovh `/suggest` can infer the artist, then fetch exact lyrics.
2. Karaoke line timing is normalized before ASS generation so adjacent lyric
   lines cannot overlap on screen.
3. Word timings are clamped to their owning line and forced into monotonic
   order, reducing timing drift and active-word overlap.


## alpha4.8.6 — core fixes

- Song identity uses the original uploaded filename, not internal `source.*`.
- LTR subtitles render as a complete line so English words cannot collide.
- Active-word highlighting overlays the same full line.
- Precision Sync aligns each canonical lyric line separately.
- Line-by-line worker segments are consumed directly instead of being
  fuzzy-rematched a second time.
- Timing overlap repair shortens the previous line first and avoids cumulative
  forward drift.


## alpha4.9 — Anchor Sync

Synchronization now treats lyrics text and timing as two separate problems.

- Reliable/provider/manual lyrics remain the only text authority.
- If LRCLIB supplies synced lyrics whose track duration matches the uploaded
  media, those line timestamps are used directly.
- Otherwise faster-whisper runs a lightweight word-timestamp pass on the
  isolated vocal stem. Its words are used only as time anchors.
- Canonical lyrics are globally sequence-aligned to those anchors; unmatched
  canonical words are interpolated between real anchors.
- Old forced-alignment transcripts are rejected by cache compatibility checks.
- The ASR anchor cache depends on audio/model/language, not lyric text, so lyric
  edits reuse the same timing pass.
- Output filename is recomputed after provider identity resolution, avoiding
  late `Unknown Artist` names when the provider identifies the artist.
