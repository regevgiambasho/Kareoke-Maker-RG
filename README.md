# Karaoke Maker — Hebrew + English

A local Streamlit app that:

1. accepts an audio/video file you are authorized to process;
2. separates vocals from the instrumental;
3. estimates word timing with WhisperX;
4. aligns lyrics that **you provide** to the singing;
5. creates an instrumental WAV and an MP4 with word-by-word highlighting.

It intentionally does **not** download audio/video from YouTube.

## Requirements

- Windows 10/11 recommended
- Python 3.11 recommended
- FFmpeg + ffprobe
- Internet on first run so AI models can download
- NVIDIA GPU optional but strongly recommended for speed

Pinned versions:
- Streamlit 1.66.1
- WhisperX 3.8.6
- audio-separator 0.48.0

## Install on Windows

Open PowerShell in this folder:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup_windows.ps1
```

If FFmpeg was installed during this step, close PowerShell, reopen it in this folder, and run the setup command again.

Then launch:

```powershell
.\run_windows.bat
```

## How to use

1. Upload MP3/WAV/FLAC/M4A or MP4/MKV/MOV/etc.
2. Paste lyrics that you are authorized to use.
3. Choose Auto / Hebrew / English.
4. Choose vocal removal quality.
5. Choose timing model:
   - `small` = faster
   - `medium` = recommended default
   - `large-v3` = best with a capable NVIDIA GPU
6. Click **Create karaoke**.
7. Download:
   - `karaoke.mp4`
   - `instrumental.wav`
   - `karaoke.ass`

## Hebrew support

WhisperX currently includes a default Hebrew alignment model. The app writes Unicode ASS subtitles and adds an RTL marker to Hebrew lines.

On Windows, `Arial` is a good default. You can type another installed Hebrew-capable font in the sidebar.

## How timing works

WhisperX estimates word timestamps internally. The app does not export an automatically transcribed song lyric. Instead, it fuzzy-matches the words **you supplied** to those timestamps and interpolates any missed words.

## Troubleshooting

### FFmpeg not found

Run:

```powershell
winget install -e --id Gyan.FFmpeg
```

Then close and reopen the terminal.

### Out of memory

Use:
- `Fast / balanced` vocal removal
- `small` or `medium` timing model
- a shorter source file

### Bad lyric sync

Check:
- correct song/version
- complete lyrics
- correct language selection

Live recordings, choirs, reverb, doubled vocals, or very heavy mastering can reduce timing quality.

### Vocal bleed remains

Try `Higher quality / slower`.

## Rights

Use this project only with media and lyrics that you own, are licensed to use, or that are public domain.
