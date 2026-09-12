"""Orchestrates frame fetching and rabbit classification."""
from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from .classifier import RabbitClassifier
from .frame_fetcher import JPEG_FETCHERS, decode_jpeg

log = logging.getLogger("rabbit-recognition")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class RecognitionResult:
    rabbit: bool
    probability: float
    threshold: float
    checked_at: str
    stream_url: str
    model: str
    frame: dict
    image_b64: str

    def to_dict(self) -> dict:
        return {
            "rabbit": self.rabbit,
            "probability": self.probability,
            "threshold": self.threshold,
            "checked_at": self.checked_at,
            "stream_url": self.stream_url,
            "model": self.model,
            "frame": self.frame,
            "image": self.image_b64,
        }


class RabbitRecognitionService:
    """Fetches frames from the Hasen-Stream and classifies them for rabbits."""

    def __init__(
        self,
        stream_url: str,
        classifier: RabbitClassifier,
        threshold: float | None = None,
        stream_timeout: float = 20.0,
        method: str = "mjpeg",
    ):
        self.stream_url = stream_url.rstrip("/")
        self.classifier = classifier
        self.threshold = threshold
        self.stream_timeout = stream_timeout
        self.method = method

    def fetch_jpeg(self, method: str | None = None, timeout: float | None = None) -> bytes:
        fetcher = JPEG_FETCHERS.get(method or self.method)
        if fetcher is None:
            raise ValueError(
                f"Unknown fetch method {method!r} (available: {sorted(JPEG_FETCHERS)})"
            )
        return fetcher(self.stream_url, timeout=(timeout or self.stream_timeout))

    def classify_jpeg(self, jpeg: bytes, threshold: float | None = None) -> RecognitionResult:
        started = time.monotonic()
        frame = decode_jpeg(jpeg)
        is_rabbit, proba, used_threshold = self.classifier.classify(
            frame, threshold=(threshold if threshold is not None else self.threshold)
        )
        height, width = frame.shape[:2]
        seconds = time.monotonic() - started
        log.info(
            "rabbit=%s p=%.3f (threshold %.2f, %dx%d, %.2fs)",
            is_rabbit, proba, used_threshold, width, height, seconds,
        )
        return RecognitionResult(
            rabbit=is_rabbit,
            probability=proba,
            threshold=used_threshold,
            checked_at=_utc_now_iso(),
            stream_url=self.stream_url,
            model=self.classifier.name,
            frame={"width": width, "height": height, "jpeg_bytes": len(jpeg)},
            image_b64=base64.b64encode(jpeg).decode("ascii"),
        )

    def recognize(
        self,
        method: str | None = None,
        threshold: float | None = None,
        timeout: float | None = None,
    ) -> RecognitionResult:
        return self.classify_jpeg(self.fetch_jpeg(method, timeout), threshold=threshold)
