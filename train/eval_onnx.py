"""Evaluate the exported ONNX model and check ONNX Runtime vs PyTorch parity.

Usage:
  python eval_onnx.py --data /path/to/TestData

Runs every image through the same preprocessed tensor on both ONNX Runtime
(exactly like the deployed classifier) and PyTorch (when a checkpoint is
given), so the parity check compares pure runtime differences. Preprocessing
matches the deployed classifier: aspect-preserving resize (short edge to 224),
center crop, /255, ImageNet normalization (mean/std from the manifest).
"""
import argparse
import glob
import json
from pathlib import Path

import numpy as np
import onnxruntime as ort
from PIL import Image

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"


def preprocess(path, size, mean, std):
    image = Image.open(path).convert("RGB")
    w, h = image.size
    scale = size / min(w, h)
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    image = image.resize((nw, nh), Image.Resampling.BILINEAR)
    x0 = (nw - size) // 2
    y0 = (nh - size) // 2
    image = image.crop((x0, y0, x0 + size, y0 + size))
    arr = np.asarray(image, dtype=np.float32) / 255.0
    arr = (arr - mean) / std
    return np.ascontiguousarray(arr.transpose(2, 0, 1), dtype=np.float32)


def report(probs, labels, threshold):
    preds = [1 if p >= threshold else 0 for p in probs]
    tp = sum(1 for p, l in zip(preds, labels) if p == 1 and l == 1)
    fp = sum(1 for p, l in zip(preds, labels) if p == 1 and l == 0)
    fn = sum(1 for p, l in zip(preds, labels) if p == 0 and l == 1)
    tn = sum(1 for p, l in zip(preds, labels) if p == 0 and l == 0)
    precision = tp / (tp + fp) if tp + fp else float("nan")
    recall = tp / (tp + fn) if tp + fn else float("nan")
    accuracy = (tp + tn) / (tp + fp + fn + tn)
    print(f"threshold {threshold:.2f}: TP={tp} FP={fp} FN={fn} TN={tn}")
    print(f"precision {precision:.4f}  recall {recall:.4f}  accuracy {accuracy:.4f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--threshold", type=float, default=0.5)
    parser.add_argument("--model", default=str(MODELS_DIR / "mobilenet_v2_rabbit.onnx"))
    parser.add_argument("--weights", default=None, help="PyTorch .pth checkpoint (optional, for parity check)")
    args = parser.parse_args()

    manifest = json.loads((Path(args.model).parent / "manifest.json").read_text())
    size = int(manifest["input"]["size"][0])
    mean = np.array(manifest["input"]["mean"], dtype=np.float32)
    std = np.array(manifest["input"]["std"], dtype=np.float32)

    samples = []
    for folder, label in (("RabbitPictures", 1), ("NoRabbitPictures", 0)):
        for path in sorted(glob.glob(str(Path(args.data) / folder / "*"))):
            if path.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                samples.append((path, label))
    print(f"Evaluating {len(samples)} images")

    tensors = [preprocess(path, size, mean, std)[None] for path, _ in samples]
    labels = [label for _, label in samples]

    session = ort.InferenceSession(args.model, providers=["CPUExecutionProvider"])
    ort_probs = [float(session.run(None, {"input": t})[0].flatten()[0]) for t in tensors]

    print("\nONNX Runtime results:")
    report(ort_probs, labels, args.threshold)

    if args.weights:
        import torch
        from torchvision.models import mobilenet_v2

        model = mobilenet_v2(weights=None)
        model.classifier[1] = torch.nn.Linear(1280, 1)
        state = torch.load(args.weights, map_location="cpu")
        if "model" in state:
            state = state["model"]
        model.load_state_dict(state)
        model.eval()

        with torch.no_grad():
            torch_probs = [
                float(torch.sigmoid(model(torch.from_numpy(t))).flatten()[0]) for t in tensors
            ]
        diffs = [abs(a - b) for a, b in zip(torch_probs, ort_probs)]
        print(f"\nPyTorch vs ONNX parity: max |diff| = {max(diffs):.2e}")
        print("PyTorch results:")
        report(torch_probs, labels, args.threshold)


if __name__ == "__main__":
    main()
