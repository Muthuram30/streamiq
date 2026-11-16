"""Local StreamIQ media library. Run with the project's virtual environment."""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import uuid
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
UPLOADS = DATA / "uploads"
CATALOG = DATA / "catalog"
DB = DATA / "streamiq.sqlite3"
PROCESSED = DATA / "processed"
MAX_UPLOADS = 3
MAX_CATALOG = 10
MAX_FILE_SIZE = 1024 * 1024 * 1024
CHUNK = 1024 * 1024
VIDEO_TYPES = {".mp4": "video/mp4", ".webm": "video/webm", ".ogg": "video/ogg"}
lock = threading.RLock()


def database():
    connection = sqlite3.connect(DB, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def initialize():
    UPLOADS.mkdir(parents=True, exist_ok=True)
    CATALOG.mkdir(parents=True, exist_ok=True)
    PROCESSED.mkdir(parents=True, exist_ok=True)
    with closing(database()) as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS uploads (
            id TEXT PRIMARY KEY, filename TEXT NOT NULL, stored TEXT NOT NULL UNIQUE,
            size INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        connection.execute("""CREATE TABLE IF NOT EXISTS playback (
            section TEXT NOT NULL, media_id TEXT NOT NULL, position REAL NOT NULL,
            duration REAL NOT NULL, plays INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (section, media_id)
        )""")
        connection.commit()


def file_entry(identifier, name, size, section, source=None):
    media_url = f"/media/{section}/{quote(identifier)}"
    processed = efficient_path(section, identifier)
    poster = poster_path(section, identifier)
    ready = processed.is_file() and (source is None or
                                     (source.is_file() and processed.stat().st_mtime >= source.stat().st_mtime))
    return {"id": identifier, "name": name, "size": size, "section": section,
            "posterUrl": f"/poster/{section}/{quote(identifier)}" if poster.is_file() else None,
            "url": media_url, "efficientUrl": media_url + "?quality=efficient"
            if ready else None}


def efficient_path(section, identifier):
    import hashlib
    key = hashlib.sha256(f"{section}/{identifier}".encode()).hexdigest()
    return PROCESSED / f"{key}-480p.mp4"


def poster_path(section, identifier):
    return efficient_path(section, identifier).with_suffix(".jpg")


def valid_video(path):
    if not shutil.which("ffprobe"):
        return False
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=codec_name", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=20, check=True)
        return bool(result.stdout.strip())
    except (subprocess.SubprocessError, OSError):
        return False


def catalog_items():
    files = sorted((p for p in CATALOG.iterdir()
                    if p.is_file() and p.suffix.lower() in VIDEO_TYPES), key=lambda p: p.name.lower())
    with closing(database()) as connection:
        play_counts = {row["media_id"]: row["plays"] for row in connection.execute(
            "SELECT media_id, plays FROM playback WHERE section='catalog'")}
    visible = files[:MAX_CATALOG]
    visible.sort(key=lambda p: (-play_counts.get(p.name, 0), p.name.lower()))
    return [file_entry(p.name, p.stem.replace("_", " "), p.stat().st_size, "catalog", p)
            for p in visible]


def upload_items():
    with closing(database()) as connection:
        rows = connection.execute("SELECT id, filename, stored, size FROM uploads ORDER BY created_at DESC, rowid DESC").fetchall()
    return [file_entry(row["id"], row["filename"], row["size"], "uploads", UPLOADS / row["stored"])
            for row in rows]


def media_exists(section, identifier):
    if section == "catalog":
        return identifier in {item["id"] for item in catalog_items()}
    if section == "uploads":
        with closing(database()) as connection:
            return connection.execute("SELECT 1 FROM uploads WHERE id = ?", (identifier,)).fetchone() is not None
    return False


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def respond(self, code, body=b"", content_type="application/json", headers=None, head=False):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store" if content_type == "application/json" else "private, max-age=3600")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def error(self, code, message):
        self.respond(code, json.dumps({"error": message}).encode())

    def do_HEAD(self):
        self.route(head=True)

    def do_GET(self):
        self.route()

    def route(self, head=False):
        path = urlparse(self.path).path
        if path == "/api/library":
            body = json.dumps({"catalog": catalog_items(), "uploads": upload_items(),
                               "uploadLimit": MAX_UPLOADS, "catalogLimit": MAX_CATALOG}).encode()
            return self.respond(200, body, head=head)
        if path == "/api/playback":
            query = parse_qs(urlparse(self.path).query)
            section = query.get("section", [""])[0]
            identifier = query.get("id", [""])[0]
            if not media_exists(section, identifier):
                return self.error(404, "Video not found")
            with closing(database()) as connection:
                row = connection.execute("SELECT position, duration, plays FROM playback WHERE section=? AND media_id=?",
                                         (section, identifier)).fetchone()
            return self.respond(200, json.dumps(dict(row) if row else {"position": 0, "duration": 0, "plays": 0}).encode(), head=head)
        if path.startswith("/media/"):
            return self.serve_media(path, head)
        if path.startswith("/poster/"):
            parts = path.split("/")
            if len(parts) != 4 or not media_exists(parts[2], unquote(parts[3])):
                return self.error(404, "Poster not found")
            poster = poster_path(parts[2], unquote(parts[3]))
            if not poster.is_file():
                return self.error(404, "Poster not found")
            return self.respond(200, poster.read_bytes(), "image/jpeg", head=head)
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8")}
        if path not in assets:
            return self.error(404, "Not found")
        filename, content_type = assets[path]
        self.respond(200, (ROOT / filename).read_bytes(), content_type, head=head)

    def serve_media(self, url_path, head):
        parts = url_path.split("/")
        if len(parts) != 4:
            return self.error(404, "Video not found")
        section, identifier = parts[2], unquote(parts[3])
        if section == "catalog":
            path = CATALOG / identifier
            if path.name != identifier or path.suffix.lower() not in VIDEO_TYPES:
                return self.error(404, "Video not found")
            if identifier not in {p["id"] for p in catalog_items()}:
                return self.error(404, "Video not found")
        elif section == "uploads":
            with closing(database()) as connection:
                row = connection.execute("SELECT stored FROM uploads WHERE id = ?", (identifier,)).fetchone()
            if row is None:
                return self.error(404, "Video not found")
            path = UPLOADS / row["stored"]
        else:
            return self.error(404, "Video not found")
        if parse_qs(urlparse(self.path).query).get("quality") == ["efficient"]:
            processed = efficient_path(section, identifier)
            if not processed.is_file() or processed.stat().st_mtime < path.stat().st_mtime:
                return self.error(404, "Efficient version is not ready")
            path = processed
        try:
            size = path.stat().st_size
            if not path.is_file():
                raise FileNotFoundError
        except OSError:
            return self.error(404, "Video not found")
        start, end, status = 0, max(size - 1, 0), 200
        range_header = self.headers.get("Range")
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
            if not match or (not match[1] and not match[2]):
                return self.respond(416, headers={"Content-Range": f"bytes */{size}"}, head=head)
            if match[1]:
                start = int(match[1])
                end = int(match[2]) if match[2] else size - 1
            else:
                start = max(0, size - int(match[2]))
            if start >= size or end < start or not size:
                return self.respond(416, headers={"Content-Range": f"bytes */{size}"}, head=head)
            end = min(end, size - 1)
            status = 206
        length = end - start + 1 if size else 0
        self.send_response(status)
        self.send_header("Content-Type", VIDEO_TYPES[path.suffix.lower()])
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("X-Content-Type-Options", "nosniff")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if head:
            return
        try:
            with path.open("rb") as stream:
                stream.seek(start)
                remaining = length
                while remaining:
                    block = stream.read(min(CHUNK, remaining))
                    if not block:
                        break
                    self.wfile.write(block)
                    remaining -= len(block)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_POST(self):
        route = urlparse(self.path).path
        if route == "/api/playback":
            return self.save_playback()
        if route != "/api/uploads":
            return self.error(404, "Not found")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self.error(411, "A file size is required")
        if length < 1 or length > MAX_FILE_SIZE:
            return self.error(413, "Choose a video smaller than 1 GB")
        filename = parse_qs(urlparse(self.path).query).get("name", [""])[0].strip()
        filename = Path(filename.replace("\\", "/")).name[:200]
        suffix = Path(filename).suffix.lower()
        if not filename or suffix not in VIDEO_TYPES:
            return self.error(415, "Choose an MP4, WebM, or Ogg video")
        identifier = uuid.uuid4().hex
        stored = identifier + suffix
        temporary = UPLOADS / (stored + ".part")
        destination = UPLOADS / stored
        with lock:
            with closing(database()) as connection:
                count = connection.execute("SELECT COUNT(*) FROM uploads").fetchone()[0]
                if count >= MAX_UPLOADS:
                    self.close_connection = True
                    return self.error(409, "Your Drive is full. It holds 3 videos")
                try:
                    remaining = length
                    with temporary.open("xb") as output:
                        while remaining:
                            block = self.rfile.read(min(CHUNK, remaining))
                            if not block:
                                raise ConnectionError("Upload interrupted")
                            output.write(block)
                            remaining -= len(block)
                    if not valid_video(temporary):
                        temporary.unlink(missing_ok=True)
                        return self.error(415, "The file does not contain a playable video stream")
                    os.replace(temporary, destination)
                    connection.execute("INSERT INTO uploads (id, filename, stored, size) VALUES (?, ?, ?, ?)",
                                       (identifier, filename, stored, length))
                    connection.commit()
                except (OSError, sqlite3.Error, ConnectionError):
                    temporary.unlink(missing_ok=True)
                    destination.unlink(missing_ok=True)
                    self.close_connection = True
                    return self.error(500, "Upload could not be saved")
        self.respond(201, json.dumps(file_entry(identifier, filename, length, "uploads")).encode())

    def save_playback(self):
        try:
            length = int(self.headers.get("Content-Length", ""))
            if not 0 < length <= 4096:
                return self.error(413, "Invalid playback update")
            value = json.loads(self.rfile.read(length))
            section, identifier = value["section"], value["id"]
            position, duration = float(value["position"]), float(value["duration"])
            import math
            if not media_exists(section, identifier) or not all(map(math.isfinite, (position, duration))) or not 0 <= position <= duration or duration <= 0:
                return self.error(400, "Invalid playback update")
            with closing(database()) as connection:
                connection.execute("""INSERT INTO playback (section, media_id, position, duration, plays)
                    VALUES (?, ?, ?, ?, 1) ON CONFLICT(section, media_id) DO UPDATE SET
                    position=excluded.position, duration=excluded.duration,
                    plays=playback.plays + CASE WHEN ? THEN 1 ELSE 0 END,
                    updated_at=CURRENT_TIMESTAMP""",
                    (section, identifier, position, duration, bool(value.get("started", False))))
                connection.commit()
            self.respond(204)
        except (ValueError, TypeError, KeyError, json.JSONDecodeError, sqlite3.Error):
            self.error(400, "Invalid playback update")


if __name__ == "__main__":
    initialize()
    server = ThreadingHTTPServer(("127.0.0.1", 8000), Handler)
    print("StreamIQ is running at http://127.0.0.1:8000", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
