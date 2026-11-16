"""Behavior checks that do not require a listening network socket."""

import io
import json
import shutil
import subprocess
import tempfile
import unittest
from email.message import Message
from pathlib import Path

import app
import prepare


class LibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise unittest.SkipTest("FFmpeg and FFprobe are required")
        cls.original_paths = app.UPLOADS, app.CATALOG, app.PROCESSED, app.DB
        cls.temp = tempfile.TemporaryDirectory(prefix="streamiq-test-")
        root = Path(cls.temp.name)
        app.UPLOADS, app.CATALOG = root / "uploads", root / "catalog"
        app.PROCESSED, app.DB = root / "processed", root / "library.sqlite3"
        app.initialize()
        cls.sample = app.CATALOG / "sample.mp4"
        subprocess.run([
            "ffmpeg", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
            "testsrc2=size=320x180:rate=12", "-t", "1", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", "-an", "-y", str(cls.sample)
        ], check=True)

    @classmethod
    def tearDownClass(cls):
        app.UPLOADS, app.CATALOG, app.PROCESSED, app.DB = cls.original_paths
        cls.temp.cleanup()

    @staticmethod
    def request(method, path, body=b"", headers=None):
        handler = app.Handler.__new__(app.Handler)
        handler.path = path
        handler.rfile = io.BytesIO(body)
        handler.wfile = io.BytesIO()
        handler.headers = Message()
        handler.headers["Content-Length"] = str(len(body))
        for key, value in (headers or {}).items():
            handler.headers[key] = value
        handler.send_response = lambda status: setattr(handler, "status", status)
        handler.send_header = lambda key, value: None
        handler.end_headers = lambda: None
        getattr(handler, method)()
        return handler.status, handler.wfile.getvalue()

    def test_01_prepare_and_seek(self):
        item = app.catalog_items()[0]
        self.assertTrue(prepare.prepare(item, self.sample))
        self.assertIsNotNone(app.catalog_items()[0]["efficientUrl"])
        self.assertIsNotNone(app.catalog_items()[0]["posterUrl"])
        self.assertEqual(self.request("do_GET", "/poster/catalog/sample.mp4")[0], 200)
        status, body = self.request("do_GET", "/media/catalog/sample.mp4?quality=efficient",
                                    headers={"Range": "bytes=0-31"})
        self.assertEqual((status, len(body)), (206, 32))
        self.assertEqual(self.request("do_GET", "/media/catalog/sample.mp4",
                                      headers={"Range": "bytes=999999-"})[0], 416)

    def test_02_upload_limit_and_validation(self):
        self.assertEqual(self.request("do_POST", "/api/uploads?name=invalid.mp4", b"not video")[0], 415)
        content = self.sample.read_bytes()
        for index in range(3):
            status, _ = self.request("do_POST", f"/api/uploads?name=video{index}.mp4", content)
            self.assertEqual(status, 201)
        self.assertEqual(self.request("do_POST", "/api/uploads?name=extra.mp4", content)[0], 409)
        status, body = self.request("do_GET", "/api/library")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)["uploads"]), 3)

    def test_03_resume_and_recommendations(self):
        payload = json.dumps({"section": "catalog", "id": "sample.mp4", "position": 0.4,
                              "duration": 1, "started": True}).encode()
        self.assertEqual(self.request("do_POST", "/api/playback", payload)[0], 204)
        status, body = self.request("do_GET", "/api/playback?section=catalog&id=sample.mp4")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["plays"], 1)
        self.assertEqual(self.request("do_GET", "/media/catalog/../sample.mp4")[0], 404)


if __name__ == "__main__":
    unittest.main()
