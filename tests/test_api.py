"""API tests with an injected fake classifier and service (no network needed)."""
import base64
import unittest

import cv2
import numpy as np

from rabbit_recognition import config as cfg
from rabbit_recognition.api import create_app
from rabbit_recognition.config import Settings
from rabbit_recognition.service import RabbitRecognitionService

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None


def _settings(**overrides):
    base = dict(
        stream_url="http://127.0.0.1:9",
        threshold=0.5,
        host="127.0.0.1",
        port=0,
        num_threads=1,
        stream_timeout=2.0,
        model_path=cfg.DEFAULT_MODEL,
        fetch_method="mjpeg",
        log_level="INFO",
        include_image=True,
        config_file=None,
    )
    base.update(overrides)
    return Settings(**base)


class FakeClassifier:
    """Implements the classifier interface with a scriptable probability."""

    name = "fake-rabbit"
    default_threshold = 0.5

    def __init__(self, proba):
        self.proba = proba

    def classify(self, bgr, threshold=None):
        used = self.default_threshold if threshold is None else threshold
        return bool(self.proba >= used), self.proba, used


def make_jpeg():
    img = np.full((240, 320, 3), 128, dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


JPEG = make_jpeg()


class TestApi(unittest.TestCase):
    def setUp(self):
        if TestClient is None:
            self.skipTest("fastapi not installed")
        self.classifier = FakeClassifier(0.9)
        self.service = RabbitRecognitionService(
            stream_url="http://127.0.0.1:9",
            classifier=self.classifier,
            threshold=0.5,
            stream_timeout=2.0,
        )
        self.service.fetch_jpeg = lambda method="mjpeg", timeout=None: JPEG
        self.client = TestClient(
            create_app(settings=_settings(), classifier=self.classifier, service=self.service)
        )

    def test_root(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("/recognize", r.json()["endpoints"])

    def test_health(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["model"], "fake-rabbit")
        self.assertEqual(body["default_threshold"], 0.5)

    def test_recognize_detects(self):
        r = self.client.get("/recognize")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["rabbit"])
        self.assertAlmostEqual(body["probability"], 0.9)
        self.assertEqual(body["model"], "fake-rabbit")
        self.assertEqual(body["threshold"], 0.5)
        self.assertEqual(body["frame"]["width"], 320)
        self.assertEqual(body["frame"]["height"], 240)
        self.assertEqual(base64.b64decode(body["image"]), JPEG)

    def test_recognize_respects_threshold_param(self):
        self.classifier.proba = 0.4
        r = self.client.get("/recognize?threshold=0.2")
        self.assertTrue(r.json()["rabbit"])
        self.assertEqual(r.json()["threshold"], 0.2)
        r = self.client.get("/recognize?threshold=0.6")
        self.assertFalse(r.json()["rabbit"])

    def test_recognize_no_rabbit(self):
        self.classifier.proba = 0.1
        r = self.client.get("/recognize")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["rabbit"])

    def test_recognize_post(self):
        r = self.client.post("/recognize")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["rabbit"])

    def test_recognize_fetch_failure_502(self):
        def broken_fetch(method="mjpeg", timeout=None):
            raise RuntimeError("stream down")

        self.service.fetch_jpeg = broken_fetch
        r = self.client.get("/recognize")
        self.assertEqual(r.status_code, 502)
        self.assertIn("frame fetch failed", r.json()["detail"]["error"])

    def test_frame_endpoint(self):
        r = self.client.get("/frame")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "image/jpeg")
        self.assertEqual(r.content, JPEG)

    def test_frame_endpoint_fetch_failure(self):
        def broken_fetch(method="mjpeg", timeout=None):
            raise RuntimeError("boom")

        self.service.fetch_jpeg = broken_fetch
        r = self.client.get("/frame")
        self.assertEqual(r.status_code, 502)

    def test_invalid_method_422(self):
        r = self.client.get("/recognize?method=bogus")
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()
