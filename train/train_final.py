"""Retrain the deployment model from labeled images and export it to ONNX.

Expects a directory layout like the rabbitRecognition TestData:
  <data>/RabbitPictures/*.jpg     -> positive (rabbit visible)
  <data>/NoRabbitPictures/*.jpg   -> negative (no rabbit)

Usage:
  python train_final.py --data /path/to/TestData

Writes:
  ../models/mobilenet_v2_rabbit.onnx
  ../models/manifest.json

Recipe (validated during research, see train/README.md):
  - torchvision MobileNetV2 with ImageNet weights, binary head (1280 -> 1)
  - default 20 epochs, batch 16, augmentation (RandomResizedCrop, hflip, color jitter)
  - capped inverse-frequency class weights (neg capped at 5), Adam (backbone 1e-4, head 1e-2)
  - FP32 ONNX export via the dynamo exporter (opset 18); also saves the
    PyTorch checkpoint (mobilenet_v2_rabbit.pth) for parity checks
"""
import argparse
import datetime
import json
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from PIL import Image
from torch.utils.data import DataLoader, Dataset
from torchvision import transforms
from torchvision.models import MobileNet_V2_Weights, mobilenet_v2

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
SEED = 1337


class ImageDataset(Dataset):
    def __init__(self, samples, class_weight, transform):
        self.samples = samples
        self.class_weight = class_weight
        self.transform = transform

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        path, label = self.samples[idx]
        image = Image.open(path).convert("RGB")
        return (
            self.transform(image),
            torch.tensor(label, dtype=torch.float32),
            torch.tensor(self.class_weight[idx], dtype=torch.float32),
        )


class SigmoidHead(nn.Module):
    """Wrapper that turns the raw logit into the final rabbit probability."""

    def __init__(self, model):
        super().__init__()
        self.model = model

    def forward(self, x):
        return torch.sigmoid(self.model(x))


def collect_samples(data_dir):
    data_dir = Path(data_dir)
    samples = []
    for folder, label in (("RabbitPictures", 1), ("NoRabbitPictures", 0)):
        folder_path = data_dir / folder
        if not folder_path.is_dir():
            raise SystemExit(f"Missing folder: {folder_path}")
        for path in sorted(folder_path.iterdir()):
            if path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
                samples.append((str(path), label))
    if not samples:
        raise SystemExit(f"No images found under {data_dir}")
    return samples


def build_model():
    model = mobilenet_v2(weights=MobileNet_V2_Weights.IMAGENET1K_V1)
    model.classifier[1] = nn.Linear(1280, 1)
    return model


def export_onnx(model, onnx_path):
    # dynamo=True: the legacy exporter down-converts from opset 18, and that
    # version conversion silently corrupts the graph (RuntimeError in
    # onnx version_converter, output no longer matches PyTorch).
    # The dynamo exporter natively emits opset 18, so request 18 to avoid any
    # (failing) down-conversion; ORT on the Pi 3 (aarch64) runs opset 18 fine.
    model = model.cpu()
    model.eval()
    wrapper = SigmoidHead(model)
    dummy = torch.randn(1, 3, 224, 224)
    torch.onnx.export(
        wrapper,
        (dummy,),
        str(onnx_path),
        input_names=["input"],
        output_names=["rabbit_probability"],
        opset_version=18,
        dynamo=True,
    )


def main():
    parser = argparse.ArgumentParser(description="Retrain the rabbit classifier and export ONNX.")
    parser.add_argument("--data", required=True, help="Path to the labeled image directory (TestData)")
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--device", default="mps", choices=["mps", "cpu"])
    args = parser.parse_args()

    torch.manual_seed(SEED)
    np.random.seed(SEED)

    if args.device == "mps" and not torch.backends.mps.is_available():
        args.device = "cpu"
    device = torch.device(args.device)

    samples = collect_samples(args.data)
    labels = np.array([label for _, label in samples], dtype=np.float32)
    positive = int(labels.sum())
    negative = len(labels) - positive
    print(f"Collected {len(samples)} images ({positive} positive, {negative} negative)")

    # Inverse-frequency class weights, capped: an uncapped 20:1 weighting makes
    # "predict no rabbit everywhere" the loss minimum and the model collapses.
    pos_weight = negative / positive
    neg_weight = min(positive / negative, 5.0)
    class_weight = np.where(labels == 1, pos_weight, neg_weight).astype(np.float32)

    # NOTE: Normalize is mandatory here; the deployment preprocessing in
    # recognition/classifier.py and the manifest must stay in sync with this.
    transform = transforms.Compose([
        transforms.RandomResizedCrop(224),
        transforms.RandomHorizontalFlip(),
        transforms.ColorJitter(0.2, 0.2, 0.2, 0.05),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ])
    dataset = ImageDataset(samples, class_weight, transform)
    loader = DataLoader(dataset, batch_size=args.batch_size, shuffle=True)

    model = build_model().to(device)
    backbone_params = [p for n, p in model.named_parameters() if not n.startswith("classifier")]
    head_params = [p for n, p in model.named_parameters() if n.startswith("classifier")]
    optimizer = torch.optim.Adam([
        {"params": backbone_params, "lr": 1e-4},
        {"params": head_params, "lr": 1e-2},
    ])
    criterion = nn.BCEWithLogitsLoss(reduction="none")

    model.train()
    for epoch in range(args.epochs):
        running_loss = 0.0
        for images, labels_batch, weights in loader:
            images = images.to(device)
            labels_batch = labels_batch.to(device)
            weights = weights.to(device)
            optimizer.zero_grad()
            logits = model(images).flatten()
            loss = (criterion(logits, labels_batch) * weights).mean()
            loss.backward()
            optimizer.step()
            running_loss += loss.item() * images.size(0)
        print(f"Epoch {epoch + 1}/{args.epochs} - loss {running_loss / len(dataset):.4f}")

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    onnx_path = MODELS_DIR / "mobilenet_v2_rabbit.onnx"
    export_onnx(model, onnx_path)
    pth_path = MODELS_DIR / "mobilenet_v2_rabbit.pth"
    torch.save(model.state_dict(), pth_path)
    print(f"Saved {pth_path}")

    manifest = {
        "name": "mobilenet_v2_rabbit",
        "architecture": "torchvision MobileNetV2 (fine-tuned binary head, sigmoid output)",
        "input": {
            "name": "input",
            "size": [224, 224],
            "channels": 3,
            "format": "CHW float32, RGB, scaled to 0-1, ImageNet normalized",
            "mean": [0.485, 0.456, 0.406],
            "std": [0.229, 0.224, 0.225],
        },
        "output": {
            "name": "rabbit_probability",
            "shape": [1],
            "description": "sigmoid probability that a rabbit is visible",
        },
        "default_threshold": 0.5,
        "trained": {
            "date": datetime.date.today().isoformat(),
            "positive": positive,
            "negative": negative,
            "source": "labeled TestData (RabbitPictures / NoRabbitPictures)",
            "epochs": args.epochs,
            "seed": SEED,
        },
    }
    (MODELS_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Exported {onnx_path}")
    print(f"Wrote {MODELS_DIR / 'manifest.json'}")


if __name__ == "__main__":
    main()
