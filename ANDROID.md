# Android use

## Option A — same Wi-Fi, no cloud cost

Run `run_lan_windows.bat` on the Windows PC.

Find the PC's IPv4 address with:

```cmd
ipconfig
```

Then browse from Android to:

```text
http://PC-IP:8501
```

Example:

```text
http://192.168.1.25:8501
```

The PC performs the AI work; Android only uploads the file and displays/downloads the result.

## Option B — independent cloud app

Deploy the included Dockerfile to a Docker-capable hosting platform with enough RAM, and preferably a GPU.

The container listens on port `7860`.

After deployment, open its HTTPS URL in Android Chrome and add it to the home screen.
