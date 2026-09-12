# train/

Retrains the deployment model from labeled images and exports it as ONNX.

## Dependencies

Install in a dedicated venv (e.g. the local `.venv` used during research):

```bash
python3 -m venv venv-recognition
venv-recognition/bin/pip install -r train/requirements.txt
```

## Run

```bash
venv-recognition/bin/python train/train_final.py --data /path/to/TestData
```

Expected data layout (JPEG images only):

```
TestData/
  RabbitPictures/      positive: rabbit visible
  NoRabbitPictures/    negative: no rabbit
```

## Outputs

- `../models/mobilenet_v2_rabbit.onnx`
- `../models/manifest.json` (input size, normalization, threshold, training metadata)

`classifier.py` reads everything it needs from the manifest, so no code
change is required after retraining.

## Recipe

- torchvision `mobilenet_v2`, `IMAGENET1K_V1` weights, binary head (1280 -> 1)
- default 20 epochs, batch 16, seed 1337
- Augmentation: RandomResizedCrop(224), horizontal flip, light color jitter
- Loss: BCEWithLogitsLoss with capped inverse-frequency class weights
- Optimizer: Adam, backbone 1e-4 / head 1e-2
- Export: FP32 ONNX via the dynamo exporter, opset 18, input `input`
  [1,3,224,224] (CHW, 0-1, ImageNet normalized), output
  `rabbit_probability` [1]

On Raspberry Pi 3 (Cortex-A53, no dot-product instructions) FP32 ONNX
through ONNX Runtime is the robust choice; INT8 quantization gives limited
gain on that hardware and is not the default.
