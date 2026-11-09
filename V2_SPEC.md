# Karaoke Maker V2 — Lyrics Verification & RTL Design

## New V2 pipeline

1. Separate vocals / instrumental.
2. Transcribe the vocal stem with WhisperX to obtain time anchors.
3. Detect song title / artist from media metadata or optional user override.
4. If a configured licensed lyrics API is available, request authorized full lyrics.
5. Align canonical lyrics to Whisper word timestamps with global sequence alignment.
6. Accept canonical lyrics only when alignment confidence passes the threshold.
7. Preserve source lyric line breaks; split only unusually long lines at natural punctuation.
8. If verification is unavailable or weak, use phrase-aware Whisper fallback.
9. Render each word as an independently positioned ASS event.
10. Render with `ass=...:shaping=complex` for Hebrew/complex scripts.

## Why the Hebrew renderer changed

V1 inserted many karaoke timing tags into one RTL sentence. ASS override runs can interact badly with bidirectional layout and highlight order. V2 positions each word explicitly:
- Hebrew logical word 1 is placed at the right edge.
- Following words are placed progressively left.
- Base text is visible for the full line.
- The currently sung word is overlaid in the highlight color for its own time interval.

This keeps word order and highlight direction deterministic.

## Licensed lyrics API contract

Set:

```text
LICENSED_LYRICS_API_URL=https://provider.example/api/lyrics
LICENSED_LYRICS_API_TOKEN=...
```

Karaoke Maker sends a POST JSON request:

```json
{
  "title": "Song title",
  "artist": "Artist",
  "language": "he",
  "transcript_hint": "short identification hint"
}
```

Expected JSON response:

```json
{
  "lyrics": "authorized full lyrics",
  "title": "Song title",
  "artist": "Artist",
  "source": "provider-name",
  "licensed": true
}
```

The application rejects full lyrics unless `licensed` or `authorized` is explicitly true.


## Provider priority

V2 uses this order:

1. **Musixmatch official API** when `MUSIXMATCH_API_KEY` is configured and title/artist are known.
2. Optional generic licensed provider (`LICENSED_LYRICS_API_URL`).
3. Whisper fallback with the result clearly marked as unverified.
4. Manual Lyrics Editor corrections.

Musixmatch integration uses `matcher.lyrics.get` with song title and artist. V2 rejects
responses that are restricted or obviously truncated rather than treating partial text
as canonical lyrics.

Shironet / Tab4U are **not scraped**. They can be added later only through an official
integration or authorized data feed.


## Song identification

If the uploaded media has no usable title/artist metadata, V2 can optionally
identify the recording using AudD's official music-recognition API.

- Requires `AUDD_API_TOKEN`.
- The user must explicitly enable the checkbox in Advanced Settings.
- Only a short compressed audio clip is sent to the recognition provider.
- Returned title/artist are then used for:
  1. the default output filename;
  2. the licensed lyrics lookup;
  3. project metadata.

If recognition is disabled, unavailable, or fails, the user can enter
Song title and Artist manually. Whisper transcription remains the final fallback.


## Video orientation

Phone videos may store their pixels rotated and rely on MP4 display-matrix
metadata to tell the player how to display them.

V2 now:
1. reads `rotate` / display-matrix metadata with ffprobe;
2. disables FFmpeg's implicit autorotation;
3. applies the required 90/180/270-degree filter explicitly;
4. burns subtitles after orientation is corrected;
5. writes the final video with rotation metadata reset to zero.

This makes orientation deterministic across Streamlit, browsers, phones and
downloaded MP4 playback.


## Large files in Codespaces

GitHub Codespaces forwarded ports can reject large browser request bodies
before they reach Streamlit. Therefore V2 supports two input modes:

1. **Browser upload** — normal Streamlit file uploader.
2. **Codespace file** — reads a media file directly from `input_media/`.

The second mode is intended for development/testing in Codespaces. The
production architecture should use direct/resumable upload to object
storage rather than routing large media through the Streamlit process.


## Seamless chunked upload

The production-facing UI has one uploader only.

- The browser slices media into 4 MB chunks.
- Each chunk is uploaded as its own small HTTP request.
- Failed chunks retry automatically.
- Existing chunks are detected so interrupted uploads can resume.
- The server verifies the final assembled byte size before exposing the file.
- The upload API is mounted on the same Streamlit origin through `st.App`
  custom routes, so there is no second port or user-visible backend.
- Developer-only `Codespace file` is removed from the normal UI.

This preserves a simple UX:
`Drop file → Upload progress → Ready → Create Karaoke`.


## Manual lyrics workflow

V2 now exposes manual lyrics in two places:

1. **Before processing** — Advanced Settings → Use manual lyrics.
   The pasted text becomes authoritative; Whisper is used only for timing.
2. **After processing** — Lyrics editor → Quick text edit.
   The entire lyric text can be replaced and only subtitle/render stages rerun.

A separate Advanced timing tab remains available for line-by-line start/end edits.

## Verified lyrics policy

V2 no longer silently implies that Whisper fallback is verified.

- If no trusted provider is configured, the UI shows an explicit warning.
- `Require verified lyrics` stops the build rather than falling back to Whisper.
- `Verified preferred` may still use Whisper fallback, but the result card marks it
  clearly as unverified.
- Manual lyrics are treated as user-authoritative text.


## Mandatory pre-render review

V2 no longer renders immediately after transcription.

Pipeline:
`Upload → Separate → Identify → Transcribe → Verify/Align → Review Lyrics → Render`

The Review screen shows:
- lyrics source and verification state;
- editable full lyric text;
- detected source rotation;
- orientation override: Auto / 0 / 90 / 180 / 270;
- suggested global sync offset;
- manual sync-offset control;
- Approve Lyrics & Render.

The expensive separation/transcription stages are not rerun after review.

## Initial sync calibration

V2 estimates first vocal onset from the isolated vocal stem with FFmpeg
`silencedetect`, compares that with the first lyric timestamp, and proposes
a global timing correction. This is presented as a suggestion rather than
silently forced, because stem bleed and musical intros can make automatic
onset detection imperfect.


## Trusted-first lyrics pipeline

Default V2 behavior is now:

1. Identify the recording with AudD when configured.
2. Use the identified title + artist for trusted lyrics lookup.
3. Prefer Musixmatch / configured licensed provider as canonical text.
4. Use Whisper primarily for timing/alignment.
5. If no trusted full lyrics are returned, stop at Review Lyrics.
   Whisper text is displayed only as a draft that must be corrected/approved.
6. Render only after the review step.

AudD may try up to `AUDD_MAX_ATTEMPTS` short clips from different points in
the song to avoid missing recognition because of a long intro.


## Musixmatch official lookup flow

V2 alpha4.2 uses the documented Musixmatch flow:

`track.search → select trustworthy title/artist match → track.lyrics.get`

The match is scored conservatively. If the track match is weak, V2 does
not attach lyrics from a different song.

The optional Musixmatch public page URL is used only as an identity hint.
V2 does not scrape lyric text from the public page.

If Musixmatch finds the track but the API response does not expose usable
full lyrics for the current account/plan, Review Lyrics shows that status
explicitly instead of silently falling back.


## Fast pipeline alpha4.3

Fast is now the default mode.

Fast mode:
- uses `UVR_MDXNET_KARA_2.onnx` for karaoke-oriented separation;
- uses all reasonable available CPU threads automatically;
- uses direct `faster-whisper` INT8 batched inference with VAD;
- when manual or trusted lyrics already exist, transcription is a
  lightweight timing pass and forced alignment is disabled;
- renders with FFmpeg `ultrafast`;
- caches stems and timing transcripts by source + model + mode.

Balanced keeps the HQ instrumental separator and WhisperX alignment.
Best Quality keeps the strongest transcription/alignment path.


## Precision Sync alpha4.4

When Fast mode has manual or trusted lyrics, V2 no longer maps those words
onto ASR guesses. It performs exact-text forced alignment directly against
the isolated vocal stem.

For Hebrew, WhisperX can load its Hebrew Wav2Vec2 alignment model. The
supplied lyric text remains authoritative; the aligner only supplies
word-level start/end timestamps.

If the exact-text aligner cannot load, the previous faster-whisper timing
path remains as a fallback.

The alignment cache key includes a SHA-256 fingerprint of the authoritative
lyric text, so editing lyrics invalidates only synchronization/rendering,
not stem separation.


## Automatic lyrics alpha4.5

Automatic lyrics now use a database-first pipeline:

1. Manual title/artist when supplied.
2. Embedded media metadata.
3. Local filename hint (`Song - Artist.ext`).
4. AudD identification when configured.
5. LRCLIB exact lookup using title + artist + duration.
6. LRCLIB structured search with conservative identity scoring.
7. Musixmatch/custom licensed provider when configured.
8. Whisper only as the last draft fallback.

LRCLIB requires no API key and may return plain, line-synced, or word-synced
community lyrics. Its text is treated as authoritative only when the
title/artist match passes a conservative score threshold. Exact-text
Precision Sync then aligns those lyrics to the isolated vocals.

LRCLIB is a community lyrics source, not a substitute for commercial lyrics
licensing when the application is distributed commercially.


## Hebrew Automatic Pipeline alpha4.6

Hebrew automatic lyrics now use: `Identity → Musixmatch → custom licensed provider → LRCLIB fallback → Whisper draft`.
Identity priority is: `Manual/Musixmatch URL → AudD validation → embedded metadata → filename`.
AudD may replace low-confidence metadata/filename guesses but never explicit fields. The old reverse filename heuristic was removed.


## Free automatic lyrics alpha4.7

`lyrics.ovh` is now a first-class keyless lyrics provider ahead of LRCLIB.

Lookup strategy:
1. Exact `artist + title`.
2. `lyrics.ovh/suggest` identity recovery.
3. Fuzzy score candidate song/artist pairs.
4. Retry exact lyrics lookup for up to four high-confidence suggestions.
5. LRCLIB fallback.
6. Whisper remains only a draft if database lookup fails.

For Hebrew, configured licensed providers still take priority over the free
providers.


## Simple Flow alpha4.8

Reliable lyrics are now a prerequisite for heavy processing.

- Lyrics lookup happens before source separation.
- If no reliable lyrics are found, project status becomes
  `awaiting_lyrics_input`.
- No separation and no ASR transcription happen in that state.
- The UI asks for manual lyrics with one text area and one Continue button.
- After manual input, the same project resumes and reuses extracted audio.
- Authoritative lyrics are synchronized with Precision Sync.
- Whisper draft lyrics are removed from the user workflow.


## Single lyrics input alpha4.8.1

There is exactly one manual-lyrics entry surface in the main workflow:
`request_manual_lyrics`.

The old Advanced Settings manual-lyrics checkbox/text area is removed.
After the initial build changes the project state, Streamlit reruns before
rendering the next state, preventing duplicate `Paste lyrics` or Review cards.


## Filename identity retry alpha4.8.2

Filename-derived identity is treated as low-confidence. On lyrics lookup
failure, the system retries the inverse mapping before requesting manual lyrics:

`X=title, Y=artist` → if no result → `Y=title, X=artist`.

Manual fields, Musixmatch URL hints, metadata, and AudD identities are not
swapped.


## Chunked Precision Sync alpha4.8.3

The previous exact-text aligner submitted the complete song audio and full
lyrics as one WhisperX segment. On CPU this could terminate during the CTC
alignment stage.

The new worker:
1. splits lyrics into roughly 20–60 word groups;
2. estimates vocal activity using lightweight RMS analysis;
3. maps lyric chunks onto cumulative vocal activity;
4. adds timing padding to each search window;
5. runs WhisperX forced alignment on each short window independently;
6. offsets local timestamps back to the full-song timeline;
7. falls back only for an individual failed chunk.

No Whisper lyric transcription is introduced.


## Auto Render alpha4.8.4

For newly created projects, `build_karaoke` is called with
`stop_before_render=False`.

When reliable or manually supplied lyrics exist, the pipeline completes
subtitles and render without a second user approval step.

UI stages:
`Upload → Extract → Identify song → Find lyrics → Separate → Sync → Subtitles → Render → Done`

Post-render lyric editing remains available.


## Stability fixes alpha4.8.5

### Title-only filename discovery
`Song.ext` is now accepted as a local identity hint. The app queries
lyrics.ovh suggestions by title, requires strong title similarity, infers the
artist, and retries exact lyrics lookup.

### Subtitle overlap prevention
Before ASS rendering, line timing is normalized so lines are monotonic and
non-overlapping. Word-level timing is clamped to each line.

This keeps the screen to one lyric line event at a time in the normal karaoke
flow and prevents successive lines from drawing on top of each other.


## Core fixes alpha4.8.6

Identity uses persistent `original_filename` metadata.

For LTR languages, ASS positioning is delegated to libass by rendering the
complete line. Active highlighting uses an identical overlay line with only
the active word recolored.

Precision Sync is line-by-line. Exact canonical line segments from the worker
are used directly. Overlap repair does not shift every later line.


## Anchor Sync alpha4.9

Primary timing strategies:

1. `provider-synced-v1`: use duration-matched synced lyrics directly.
2. `asr-anchor-v1`: faster-whisper word timestamps are used as anchors;
   canonical lyrics remain authoritative.

`forced_align_worker.py` remains only for backward compatibility and is not
called by the normal alpha4.9 build path.

Line cache records `sync_version`. A timing rebuild invalidates subtitles and
render output so stale alpha4.8 timing cannot leak into new results.


## Singer Guidance & Startup Stability alpha4.9.1

alpha4.9.1 builds on alpha4.9 Anchor Sync and changes only the presentation/render layer for singer guidance:

1. Word highlighting is rendered 120 ms early while stored canonical timestamps remain unchanged.
2. During normal line transitions, the next lyric line is shown at 68% size below the active line for up to the final 4 seconds of the current line.
3. If the gap from the previous vocal end to the next true word onset is at least 4 seconds, next-line preview is suppressed and a 3-2-1 countdown is shown instead.
4. The same countdown logic applies to musical intros before the first vocal entry.
5. FFmpeg video/audio timestamps are rebased with setpts/asetpts and the instrumental receives a 30 ms fade-in to reduce startup clicks/glitches without changing audible song timing.

The countdown target is based on the first word timestamp, not a subtitle lead-in, and ends when the 120 ms visual lead begins.


## Singer Guidance alpha4.9.2

Renderer artifacts are version-fingerprinted. A project rendered by an older renderer is not considered complete for the subtitle/render stages, so upgrades rebuild `karaoke.ass` and the final MP4 while reusing expensive separation and synchronization outputs.

Singer guidance behavior:
- active-word cue: 200 ms early;
- next-line preview: smaller line below the active lyric during normal vocal continuity;
- instrumental/intro gap threshold: 3 seconds;
- countdown: large 3 → 2 → 1 ending immediately before the visual first-word cue;
- startup audio: no fade; timestamps are normalized and audio is resampled with `first_pts=0`.


## Video Fidelity & Timing alpha4.9.3

alpha4.9.3 changes only singer-cue timing and final video encoding:

- active-word visual lead: 280 ms; canonical Anchor Sync timestamps are unchanged;
- H.264 output: CRF 14, preset `medium`, tune `film`;
- source-video renders keep the source FPS and dimensions and add no scaling/interpolation;
- countdown and next-line preview behavior are unchanged from alpha4.9.2;
- subtitle/video cache signatures are bumped so old visual artifacts rebuild without rerunning separation or synchronization.

