"""FastAPI HTTP API, intended to be triggered by n8n.

Run with:  python -m rabbit_recognition.api
(n8n then calls GET /recognize, e.g. http://<this-pi>:8011/recognize)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response

from . import __version__
from .classifier import RabbitClassifier
from .config import Settings, load_settings
from .frame_fetcher import FETCHERS  # noqa: F401  (available for reuse)
from .service import RabbitRecognitionService

log = logging.getLogger("rabbit-recognition")

PID_FILE = Path(__file__).resolve().parent.parent / "run.pid"


def create_app(
    settings: Settings | None = None,
    classifier: RabbitClassifier | None = None,
    service: RabbitRecognitionService | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    classifier = classifier or RabbitClassifier(
        model_path=settings.model_path, num_threads=settings.num_threads
    )
    service = service or RabbitRecognitionService(
        stream_url=settings.stream_url,
        classifier=classifier,
        threshold=settings.threshold,
        stream_timeout=settings.stream_timeout,
        method=settings.fetch_method,
    )

    app = FastAPI(
        title="RabbitRecognition",
        version=__version__,
        description=(
            "Fetches a frame from the Hasen-Stream backend (camera Pi, same LAN) and "
            "classifies it for rabbits with a fine-tuned MobileNetV2 ONNX model. "
            "Trigger this service from n8n (e.g. a schedule or a webhook)."
        ),
    )

    @app.get("/")
    def root():
        return {
            "service": app.title,
            "version": app.version,
            "endpoints": ["/recognize", "/frame", "/health"],
        }

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "model": classifier.name,
            "default_threshold": classifier.default_threshold,
            "stream_url": service.stream_url,
            "fetch_method": settings.fetch_method,
            "include_image": settings.include_image,
            "config_file": str(settings.config_file) if settings.config_file else None,
        }

    @app.get("/recognize")
    @app.post("/recognize")
    def recognize(
        method: Literal["mjpeg", "save"] | None = Query(
            None, description=f"Frame source endpoint (default: configured {settings.fetch_method!r})"
        ),
        threshold: float | None = Query(None, ge=0.0, le=1.0, description="Overrides the configured threshold"),
        timeout: float | None = Query(None, gt=0.0, description="Timeout (s) for the frame fetch"),
    ):
        """Fetch one frame from the stream and classify it for rabbits."""
        effective_method = method or settings.fetch_method
        try:
            jpeg = service.fetch_jpeg(method=effective_method, timeout=timeout)
        except Exception as exc:
            log.warning("Frame fetch failed: %s", exc)
            raise HTTPException(
                status_code=502,
                detail={"error": f"frame fetch failed: {exc}", "method": effective_method},
            )
        try:
            result = service.classify_jpeg(jpeg, threshold=threshold)
        except Exception as exc:
            log.exception("Classification failed")
            raise HTTPException(status_code=500, detail={"error": f"classification failed: {exc}"})
        payload = result.to_dict()
        if not settings.include_image:
            payload.pop("image", None)
        return payload

    @app.get("/frame")
    def frame(
        method: Literal["mjpeg", "save"] | None = Query(None, description="Frame source endpoint"),
        timeout: float | None = Query(None, gt=0.0),
    ):
        """Return the raw JPEG frame fetched from the stream."""
        try:
            jpeg = service.fetch_jpeg(method=method or settings.fetch_method, timeout=timeout)
        except Exception as exc:
            raise HTTPException(status_code=502, detail={"error": f"frame fetch failed: {exc}"})
        return Response(content=jpeg, media_type="image/jpeg")

    return app


app = create_app()


def main() -> None:
    settings = load_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        PID_FILE.write_text(f"{os.getpid()}\n")
    except OSError:
        pass  # run.pid is only needed for `task stop` / `task status`
    log.info(
        "rabbit-recognition: serving HTTP API on %s:%s (stream: %s)",
        settings.host, settings.port, settings.stream_url,
    )
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
