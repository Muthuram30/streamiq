# StreamIQ

![Python](https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white) ![FFmpeg](https://img.shields.io/badge/FFmpeg-497820?logo=ffmpeg&logoColor=white) ![SQLite](https://img.shields.io/badge/SQLite-196181?logo=sqlite&logoColor=white)

StreamIQ is a local video library for a small, single-user collection. **For you** plays up to 10 videos from a curated folder, and **My Drive** stores up to three videos uploaded through the browser. A compact playback copy can reduce transferred bytes when the viewer chooses Data saver or the player detects a constrained connection.

## Features

- Browser playback with pause, seeking, fullscreen controls, and HTTP byte-range responses.
- Up to 10 curated videos. Previously watched videos move higher in For you.
- Three uploaded videos in My Drive, each up to 1 GB. FFprobe checks for a video stream before accepting an upload.
- Playback position saved in SQLite. Reopening a video resumes at the saved position.
- Optional 480p H.264/AAC copies generated with FFmpeg. The player offers Auto, Original, and Data saver. Auto uses the browser's connection estimate when available and switches to Data saver after sustained buffering.
- Video thumbnails generated alongside compact playback copies.
- Local media storage outside Git. The Python app needs no pip dependencies.

## Requirements and setup

Use Python 3.11 or newer and install FFmpeg with FFprobe on `PATH`. A modern browser is required. Source videos must use codecs supported by that browser. Keep enough disk space for source files and generated copies.

```powershell
cd "D:\My Projects\streamiq"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python app.py
```

Open **http://127.0.0.1:8000**. On this machine, `.venv` is already created, so skip its creation step.

To populate For you, put up to 10 `.mp4`, `.webm`, or `.ogg` files in `data/catalog` and refresh the browser. My Drive accepts files through **Upload video**. The collections are independent.

After adding or uploading videos, generate smaller playback copies in a terminal with `.venv` activated:

```powershell
python prepare.py --all
```

Keep the command running until each video reports `Ready`. Refresh the browser to expose Data saver. Original files play before conversion finishes. Conversion takes time and disk space, particularly for long videos.

## Project files

| Path | Purpose |
| --- | --- |
| `app.py` | HTTP server, validation, SQLite metadata, byte-range playback, and playback progress |
| `prepare.py` | FFmpeg conversion to compact 480p MP4 copies |
| `index.html`, `app.js`, `style.css` | Browser interface |
| `data/catalog/` | Curated source videos |
| `data/uploads/` | Browser-uploaded source videos |
| `data/processed/` | Generated playback copies and thumbnails |
| `data/streamiq.sqlite3` | Upload records and playback progress |

Git ignores the entire `data/` folder. Back up that folder together to preserve the videos and their metadata. The app creates it on first run. There is no video deletion control.

## HTTP interface

| Endpoint | Function |
| --- | --- |
| `GET /api/library` | Curated and uploaded video listings with playback URLs |
| `POST /api/uploads?name=<filename>` | Raw video request body, limited to 1 GB and three stored uploads |
| `GET /media/{catalog\|uploads}/{id}` | Original video, with optional `Range` header |
| `GET /media/{catalog\|uploads}/{id}?quality=efficient` | Generated compact video, when ready |
| `GET /api/playback?section=<section>&id=<id>` | Saved playback progress |
| `POST /api/playback` | Update playback progress and watch count |

The server binds to `127.0.0.1`. It has no accounts or access control. Before an internet-facing deployment, add authentication, per-user quotas, HTTPS through a reverse proxy, background conversion jobs, and database migration management.

## Streaming challenge scope

The compact copy provides a real bandwidth option. StreamIQ currently chooses between complete MP4 files and can switch after buffering. HLS/DASH segment switching, content-aware encoding, perceptual-quality-per-megabyte benchmarks, and measured rebuffer and startup comparisons still need a shared clip set, a controlled network profile, and a baseline.
