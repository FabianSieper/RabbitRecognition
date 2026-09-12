"""Tests for the config layer: defaults, config file, env overrides."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from rabbit_recognition import config as cfg_mod
from rabbit_recognition.config import load_settings

_ALL_VARS = list(cfg_mod._ENV_KEYS) + ["RABBIT_CONFIG"]


@contextmanager
def _env_overrides(updates=None, clear_vars=None):
    """Set env vars and/or clear others, restoring the original state after."""
    updates = updates or {}
    clear_vars = list(clear_vars or [])
    touched = list(set(list(updates) + clear_vars))
    saved = {k: os.environ.get(k) for k in touched}
    for key, value in updates.items():
        os.environ[key] = value
    for key in clear_vars:
        if key not in updates:
            os.environ.pop(key, None)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class TestLoadSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.missing_default_file = mock.patch.object(
            cfg_mod, "DEFAULT_CONFIG_FILE", Path(self.tmp.name) / "missing.toml"
        )

    def _assert_defaults(self, s):
        self.assertEqual(s.stream_url, "http://192.168.178.135:8000")
        self.assertEqual(s.fetch_method, "mjpeg")
        self.assertEqual(s.stream_timeout, 20.0)
        self.assertEqual(s.model_path, cfg_mod.DEFAULT_MODEL)
        self.assertIsNone(s.threshold)
        self.assertEqual(s.num_threads, 2)
        self.assertEqual(s.host, "0.0.0.0")
        self.assertEqual(s.port, 8011)
        self.assertEqual(s.log_level, "INFO")
        self.assertTrue(s.include_image)

    def test_defaults(self):
        with self.missing_default_file, _env_overrides(clear_vars=_ALL_VARS):
            s = load_settings()
        self._assert_defaults(s)
        self.assertIsNone(s.config_file)

    def test_toml_file(self):
        path = Path(self.tmp.name) / "config.toml"
        path.write_text(
            "[stream]\n"
            "url = \"http://10.0.0.5:3000/\"\n"
            "method = \"save\"\n"
            "timeout = 7\n"
            "\n"
            "[model]\n"
            "path = \"models/custom.onnx\"\n"
            "# threshold omitted on purpose: stays the model default (None)\n"
            "num_threads = 4\n"
            "\n"
            "[service]\n"
            "host = \"127.0.0.1\"\n"
            "port = 9999\n"
            "log_level = \"debug\"\n"
            "include_image = false\n",
            encoding="utf-8",
        )
        with _env_overrides({"RABBIT_CONFIG": str(path)}, clear_vars=_ALL_VARS):
            s = load_settings()
        self.assertEqual(s.stream_url, "http://10.0.0.5:3000")
        self.assertEqual(s.fetch_method, "save")
        self.assertEqual(s.stream_timeout, 7.0)
        self.assertEqual(s.model_path, cfg_mod.REPO_ROOT / "models" / "custom.onnx")
        self.assertIsNone(s.threshold)
        self.assertEqual(s.num_threads, 4)
        self.assertEqual(s.host, "127.0.0.1")
        self.assertEqual(s.port, 9999)
        self.assertEqual(s.log_level, "DEBUG")
        self.assertFalse(s.include_image)
        self.assertEqual(s.config_file, path)

    def test_json_file_flat_keys(self):
        path = Path(self.tmp.name) / "config.json"
        path.write_text(
            json.dumps(
                {
                    "stream_url": "http://192.168.1.20:8000",
                    "threshold": 0.7,
                    "port": 8050,
                },
            ),
            encoding="utf-8",
        )
        with _env_overrides({"RABBIT_CONFIG": str(path)}, clear_vars=_ALL_VARS):
            s = load_settings()
        self.assertEqual(s.stream_url, "http://192.168.1.20:8000")
        self.assertEqual(s.threshold, 0.7)
        self.assertEqual(s.port, 8050)
        self.assertEqual(s.fetch_method, "mjpeg")  # untouched default
        self.assertTrue(s.include_image)  # untouched default

    def test_env_overrides_file(self):
        path = Path(self.tmp.name) / "config.toml"
        path.write_text("[service]\nport = 8011\n", encoding="utf-8")
        with _env_overrides(
            {"RABBIT_CONFIG": str(path), "RABBIT_PORT": "8022",
             "HASEN_STREAM_URL": "http://env-host:8000"},
            clear_vars=_ALL_VARS,
        ):
            s = load_settings()
        self.assertEqual(s.port, 8022)
        self.assertEqual(s.stream_url, "http://env-host:8000")

    def test_missing_rabbit_config_falls_back_to_defaults(self):
        missing = Path(self.tmp.name) / "nope.toml"
        with _env_overrides({"RABBIT_CONFIG": str(missing)}, clear_vars=_ALL_VARS):
            s = load_settings()
        self.assertIsNone(s.config_file)
        self._assert_defaults(s)

    def test_invalid_fetch_method_raises(self):
        with _env_overrides({"RABBIT_STREAM_METHOD": "bogus"}, clear_vars=_ALL_VARS):
            with self.assertRaises(ValueError):
                load_settings()


if __name__ == "__main__":
    unittest.main()
