"""Create compact MP4 playback copies for the StreamIQ library."""

import argparse
import subprocess
import sys
from pathlib import Path

import app


def sources():
    for item in app.catalog_items() + app.upload_items():
        if item["section"] == "catalog":
            source = app.CATALOG / item["id"]
        else:
            with app.closing(app.database()) as connection:
                row = connection.execute("SELECT stored FROM uploads WHERE id=?", (item["id"],)).fetchone()
            if row is None:
                continue
            source = app.UPLOADS / row["stored"]
        if source.is_file():
            yield item, source


def prepare(item, source):
    destination = app.efficient_path(item["section"], item["id"])
    poster = app.poster_path(item["section"], item["id"])
    if not poster.is_file() or poster.stat().st_mtime < source.stat().st_mtime:
        thumbnail = poster.with_suffix(".thumbnail")
        try:
            subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                            "-ss", "0.5", "-i", str(source), "-frames:v", "1",
                            "-vf", "scale=480:-2", "-c:v", "mjpeg", "-f", "image2",
                            "-update", "1", "-y", str(thumbnail)],
                           check=True)
            thumbnail.replace(poster)
        except (OSError, subprocess.CalledProcessError):
            thumbnail.unlink(missing_ok=True)
    if destination.exists() and destination.stat().st_mtime >= source.stat().st_mtime:
        print(f"Ready: {item['name']}")
        return True
    temporary = destination.with_suffix(".encoding")
    if temporary.exists():
        print(f"Encoding already in progress: {item['name']}", file=sys.stderr)
        return False
    if not app.valid_video(source):
        print(f"Skipped invalid video: {item['name']}", file=sys.stderr)
        return False
    command = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(source),
        "-map", "0:v:0", "-map", "0:a:0?", "-vf",
        "scale=854:480:force_original_aspect_ratio=decrease:force_divisible_by=2",
        "-c:v", "libx264", "-preset", "medium", "-crf", "28", "-maxrate", "900k",
        "-bufsize", "1800k", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
        "-movflags", "+faststart", "-f", "mp4", str(temporary),
    ]
    print(f"Encoding: {item['name']}", flush=True)
    try:
        subprocess.run(command, check=True)
        if not app.valid_video(temporary):
            raise ValueError("The output is not a video")
        temporary.replace(destination)
        print(f"Ready: {item['name']} ({destination.stat().st_size / 1048576:.1f} MB)")
        return True
    except (OSError, subprocess.CalledProcessError, ValueError) as error:
        temporary.unlink(missing_ok=True)
        print(f"Failed: {item['name']}: {error}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="Prepare every video in Streaming and My Drive")
    args = parser.parse_args()
    app.initialize()
    if not args.all:
        parser.error("Pass --all to prepare the library")
    items = list(sources())
    if not items:
        print("No videos yet. Add files to data/catalog or upload them in My Drive.")
        return
    failures = sum(not prepare(item, source) for item, source in items)
    if failures:
        raise SystemExit(f"{failures} video(s) could not be prepared")


if __name__ == "__main__":
    main()
