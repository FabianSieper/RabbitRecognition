"""One-shot CLI: fetch a frame, classify it, print the result."""
from __future__ import annotations

import argparse
import json
import logging
import sys

from .classifier import RabbitClassifier
from .config import load_settings
from .frame_fetcher import FETCHERS
from .service import RabbitRecognitionService


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Fetch one frame from the stream and classify it for rabbits.")
    parser.add_argument("--method", choices=sorted(FETCHERS), default=None,
                        help="Frame source endpoint (default: configured fetch method)")
    parser.add_argument("--threshold", type=float, default=None)
    parser.add_argument("--json", action="store_true", help="Print the full result as JSON")
    args = parser.parse_args(argv)

    settings = load_settings()
    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(name)s: %(message)s")
    classifier = RabbitClassifier(model_path=settings.model_path, num_threads=settings.num_threads)
    service = RabbitRecognitionService(
        stream_url=settings.stream_url,
        classifier=classifier,
        threshold=args.threshold if args.threshold is not None else settings.threshold,
        stream_timeout=settings.stream_timeout,
        method=settings.fetch_method,
    )
    try:
        result = service.recognize(method=args.method or settings.fetch_method)
    except Exception as exc:
        logging.getLogger("check").error("Recognition failed: %s", exc)
        return 1
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(
            f"rabbit={result.rabbit}  p={result.probability:.3f}  "
            f"threshold={result.threshold:.2f}  "
            f"frame={result.frame['width']}x{result.frame['height']}  "
            f"checked_at={result.checked_at}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
