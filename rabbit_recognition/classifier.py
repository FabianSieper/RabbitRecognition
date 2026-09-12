"""Rabbit classifier based on a fine-tuned MobileNetV2 ONNX model."""
import json
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
DEFAULT_MODEL = MODELS_DIR / "mobilenet_v2_rabbit.onnx"
MANIFEST = MODELS_DIR / "manifest.json"


class RabbitClassifier:
    def __init__(self, model_path=DEFAULT_MODEL, input_size=None, num_threads=2):
        manifest = self._load_manifest()
        if input_size is None:
            size = manifest.get("input", {}).get("size")
            input_size = int(size[1]) if size else 224
        self.name = manifest.get("name", Path(str(model_path)).stem)
        self.default_threshold = float(manifest.get("default_threshold", 0.5))
        self.mean = np.array(
            manifest.get("input", {}).get("mean", [0.485, 0.456, 0.406]), dtype=np.float32
        )
        self.std = np.array(
            manifest.get("input", {}).get("std", [0.229, 0.224, 0.225]), dtype=np.float32
        )
        session_options = ort.SessionOptions()
        session_options.intra_op_num_threads = num_threads
        self.session = ort.InferenceSession(
            str(model_path),
            sess_options=session_options,
            providers=["CPUExecutionProvider"],
        )
        self.input_name = self.session.get_inputs()[0].name
        self.input_size = input_size

    @staticmethod
    def _load_manifest():
        if MANIFEST.exists():
            return json.loads(MANIFEST.read_text())
        return {}

    def preprocess(self, bgr):
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        scale = self.input_size / min(h, w)
        rgb = cv2.resize(
            rgb,
            (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
            interpolation=cv2.INTER_LINEAR,
        )
        h, w = rgb.shape[:2]
        y0 = (h - self.input_size) // 2
        x0 = (w - self.input_size) // 2
        rgb = rgb[y0 : y0 + self.input_size, x0 : x0 + self.input_size]
        x = rgb.astype(np.float32) / 255.0
        x = (x - self.mean) / self.std
        x = np.transpose(x, (2, 0, 1))[None, ...]
        return x.astype(np.float32)

    def predict_proba(self, bgr):
        x = self.preprocess(bgr)
        out = self.session.run(None, {self.input_name: x})[0].flatten()
        return float(out[0])

    def classify(self, bgr, threshold=None):
        if threshold is None:
            threshold = self.default_threshold
        proba = self.predict_proba(bgr)
        return proba >= threshold, proba, threshold
