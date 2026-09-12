"""Unit + integration tests for the MJPEG frame fetcher."""
import io
import os
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

import cv2
import numpy as np

from mocks import mock_stream
from rabbit_recognition.frame_fetcher import decode_jpeg, extract_first_jpeg, fetch_jpeg_from_mjpeg


def make_jpeg(width=320, height=240):
    rng = np.random.RandomState(0)
    img = rng.randint(0, 255, (height, width, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def chunked(data, sizes):
    it = iter(sizes)
    pos = 0
    while pos < len(data):
        size = next(it, sizes[-1])
        yield data[pos:pos + size]
        pos += size
    yield b""


class TestExtractFirstJpeg(unittest.TestCase):
    def test_frame_directly_after_boundary(self):
        jpeg = make_jpeg()
        stream = (
            b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
            + jpeg
            + b"\r\n--frame\r\nContent-Type: image/jpeg\r\n\r\n"
            + jpeg
            + b"\r\n"
        )
        for sizes in ([1, 3, 7], [100, 4096], [7, 128, 65536]):
            with self.subTest(sizes=sizes):
                self.assertEqual(extract_first_jpeg(chunked(stream, sizes)), jpeg)

    def test_frame_with_part_headers(self):
        jpeg = make_jpeg()
        part = (
            b"--frame\r\nContent-Type: image/jpeg\r\n"
            b"Content-Length: " + str(len(jpeg)).encode() + b"\r\n\r\n"
            + jpeg
        )
        stream = part + b"\r\n" + part + b"\r\n"
        self.assertEqual(extract_first_jpeg(chunked(stream, [1, 5, 64])), jpeg)

    def test_stream_end_without_full_frame(self):
        jpeg = make_jpeg()
        truncated = (b"--frame\r\n\r\n" + jpeg)[: len(jpeg) // 2]
        with self.assertRaises(RuntimeError):
            extract_first_jpeg(iter([truncated, b""]))

    def test_garbage_does_not_grow_unbounded(self):
        junk = b"x" * (1 << 20) * 2
        with self.assertRaises(RuntimeError):
            extract_first_jpeg(iter([junk, junk, b""]))

    def test_decode_jpeg(self):
        jpeg = make_jpeg(160, 120)
        frame = decode_jpeg(jpeg)
        self.assertEqual(frame.shape[:2], (120, 160))
        with self.assertRaises(RuntimeError):
            decode_jpeg(b"not a jpeg")


class TestFetchFromMjpegServer(unittest.TestCase):
    """Integration test against a real HTTP MJPEG server (mock_stream contract)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        img_dir = os.path.join(cls.tmp.name, "frames")
        os.makedirs(img_dir)
        jpeg = make_jpeg()
        with open(os.path.join(img_dir, "a.jpg"), "wb") as f:
            f.write(jpeg)
        cls.mock = mock_stream.MockStream([os.path.join(img_dir, "a.jpg")])
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), mock_stream.build_handler(cls.mock))
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        small_dir = os.path.join(cls.tmp.name, "small")
        os.makedirs(small_dir)
        img = np.full((120, 160, 3), 128, dtype=np.uint8)
        ok, buf = cv2.imencode(".jpg", img)
        assert ok
        with open(os.path.join(small_dir, "s.jpg"), "wb") as f:
            f.write(buf.tobytes())
        cls.small_mock = mock_stream.MockStream([os.path.join(small_dir, "s.jpg")])
        cls.small_server = ThreadingHTTPServer(
            ("127.0.0.1", 0), mock_stream.build_handler(cls.small_mock)
        )
        cls.small_port = cls.small_server.server_address[1]
        cls.small_thread = threading.Thread(
            target=cls.small_server.serve_forever, daemon=True
        )
        cls.small_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.mock.stop()
        cls.small_mock.stop()
        cls.server.shutdown()
        cls.small_server.shutdown()
        cls.server.server_close()
        cls.small_server.server_close()
        cls.tmp.cleanup()

    def test_fetch_jpeg(self):
        jpeg = fetch_jpeg_from_mjpeg("http://127.0.0.1:%d" % self.port, timeout=10.0)
        frame = decode_jpeg(jpeg)
        self.assertIsNotNone(frame)
        self.assertEqual(frame.shape[:2], (240, 320))

    def test_fetch_small_frame_returns_promptly(self):
        # Regression: urllib3 iter_content(chunk_size=N) blocks until N bytes
        # have arrived; with a small frame (low stream bitrate) a 65 KB chunk
        # took >13 s. The fetch must return as soon as one full frame arrives.
        start = time.monotonic()
        jpeg = fetch_jpeg_from_mjpeg(
            "http://127.0.0.1:%d" % self.small_port, timeout=10.0
        )
        elapsed = time.monotonic() - start
        frame = decode_jpeg(jpeg)
        self.assertEqual(frame.shape[:2], (120, 160))
        self.assertLess(elapsed, 5.0)


if __name__ == "__main__":
    unittest.main()
