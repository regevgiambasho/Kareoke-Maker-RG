---
title: Karaoke Maker HE EN
emoji: 🎤
colorFrom: blue
colorTo: purple
sdk: docker
app_port: 7860
suggested_hardware: t4-small
---

# Karaoke Maker — Android / Cloud deployment

This repository is ready for a Docker-based hosting service such as a Hugging Face Docker Space.

The phone does not perform the AI processing. The server runs:
- audio separation,
- WhisperX word alignment,
- FFmpeg video rendering.

The Android browser is only the UI.

## Hugging Face Space

1. Create a new Space.
2. Choose **Docker** as the SDK.
3. Upload all files from this folder.
4. Copy the YAML block at the top of this file to the top of the Space's `README.md`.
5. Let the Space build.
6. For practical song processing, choose GPU hardware such as a T4 if available.
7. Open the Space URL on Android Chrome.
8. Use **Add to Home screen** if you want an app-like shortcut.

## Local Wi-Fi mode (free)

If your Windows PC is on the same Wi-Fi as your phone:

1. Install the project normally with `setup_windows.ps1`.
2. Double-click `run_lan_windows.bat`.
3. On the PC, run `ipconfig`.
4. Find the Wi-Fi adapter's IPv4 address, e.g. `192.168.1.25`.
5. On Android Chrome open `http://192.168.1.25:8501`.

Windows Firewall may ask whether to allow Python/Streamlit on private networks; allow it for your home/private network.

## Rights

Use only media and lyrics you own, are licensed to process, or that are public domain.
