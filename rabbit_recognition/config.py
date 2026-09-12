"""Runtime configuration: defaults < config file < environment variables.

The service reads a TOML (or JSON) config file — by default ``config.toml``
next to the repository root, override with the ``RABBIT_CONFIG`` environment
variable. Environment variables always win over the file, so the file is for
stable settings and the environment (e.g. a ``.env`` file) is
for one-off overrides.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

log = logging.getLogger("rabbit-recognition")

REPO_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = REPO_ROOT / "models"
DEFAULT_MODEL = MODELS_DIR / "mobilenet_v2_rabbit.onnx"
DEFAULT_CONFIG_FILE = REPO_ROOT / "config.toml"

DEFAULT_STREAM_URL = "http://192.168.178.135:8000"
VALID_FETCH_METHODS = ("mjpeg", "save")


@dataclass(frozen=True)
class Settings:
    stream_url: str
    fetch_method: str
    stream_timeout: float
    model_path: Path
    threshold: float | None
    num_threads: int
    host: str
    port: int
    log_level: str
    include_image: bool
    config_file: Path | None


DEFAULTS: dict = {
    "stream_url": DEFAULT_STREAM_URL,
    "fetch_method": "mjpeg",
    "stream_timeout": 20.0,
    "model_path": DEFAULT_MODEL,
    "threshold": None,
    "num_threads": 2,
    "host": "0.0.0.0",
    "port": 8011,
    "log_level": "INFO",
    "include_image": True,
}

# Config-file sections mapping to Settings fields. Flat top-level keys using
# the field names directly are accepted as well (handy for JSON).
_FILE_SECTIONS: dict = {
    "stream": {"url": "stream_url", "method": "fetch_method", "timeout": "stream_timeout"},
    "model": {"path": "model_path", "threshold": "threshold", "num_threads": "num_threads"},
    "service": {"host": "host", "port": "port", "log_level": "log_level", "include_image": "include_image"},
}

# Environment variables mapping to Settings fields (highest precedence).
_ENV_KEYS: dict = {
    "HASEN_STREAM_URL": "stream_url",
    "RABBIT_STREAM_METHOD": "fetch_method",
    "RABBIT_STREAM_TIMEOUT": "stream_timeout",
    "RABBIT_MODEL": "model_path",
    "RABBIT_THRESHOLD": "threshold",
    "RABBIT_NUM_THREADS": "num_threads",
    "RABBIT_HOST": "host",
    "RABBIT_PORT": "port",
    "RABBIT_LOG_LEVEL": "log_level",
    "RABBIT_INCLUDE_IMAGE": "include_image",
}


def _parse_bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _normalize(field: str, value):
    if field == "stream_url":
        return str(value).strip().rstrip("/")
    if field == "model_path":
        path = Path(str(value).strip()).expanduser()
        return path if path.is_absolute() else REPO_ROOT / path
    if field == "threshold":
        if value in (None, ""):
            return None
        return float(value)
    if field == "stream_timeout":
        return float(value)
    if field in ("num_threads", "port"):
        return int(str(value))
    if field == "include_image":
        return _parse_bool(value)
    if field == "log_level":
        return str(value).upper()
    if field == "fetch_method":
        return str(value).lower()
    return value


def _parse_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    if tomllib is None:
        raise ValueError(
            f"Cannot parse {path}: .toml config requires Python >= 3.11 (use .json)"
        )
    return tomllib.loads(text)


def load_config_file(path: Path) -> dict:
    """Parse a .toml/.json config file into {settings_field: raw_value}."""
    data = _parse_file(path)
    values: dict = {}
    for section, mapping in _FILE_SECTIONS.items():
        section_data = data.get(section) or {}
        for key, field in mapping.items():
            if key in section_data:
                values[field] = section_data[key]
    for field in DEFAULTS:
        if field in data:
            values[field] = data[field]
    return values


def find_config_file() -> Path | None:
    explicit = os.environ.get("RABBIT_CONFIG", "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if not path.exists():
            log.warning("RABBIT_CONFIG points to a missing file: %s", path)
            return None
        return path
    return DEFAULT_CONFIG_FILE if DEFAULT_CONFIG_FILE.exists() else None


def load_env() -> dict:
    values: dict = {}
    for var, field in _ENV_KEYS.items():
        raw = os.environ.get(var, "").strip()
        if raw:
            values[field] = raw
    return values


def load_settings() -> Settings:
    merged = dict(DEFAULTS)
    config_file = find_config_file()
    if config_file is not None:
        merged.update(load_config_file(config_file))
        log.debug("Loaded config file %s", config_file)
    merged.update(load_env())
    normalized = {field: _normalize(field, merged[field]) for field in DEFAULTS}
    if normalized["fetch_method"] not in VALID_FETCH_METHODS:
        raise ValueError(
            f"Unknown fetch method {normalized['fetch_method']!r} "
            f"(available: {', '.join(VALID_FETCH_METHODS)})"
        )
    return Settings(**normalized, config_file=config_file)
