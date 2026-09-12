# CONCEPT.md — RabbitRecognition: central design & status document

Single source of truth for everything planned for this project. Each item
carries a status so the remaining work stays visible until it is done.

Status legend: ✅ implemented & tested · 🔧 in progress · ⬜ not started ·
👤 user action required (no code needed)

## 1. Goal

Standalone HTTP service that:

- is **triggered by n8n** via an endpoint the service itself serves,
- fetches **one frame** from the Hasen-Stream backend on the camera Pi,
- classifies it ("rabbit visible" / "no rabbit") with the fine-tuned
  MobileNetV2 ONNX model,
- answers with JSON (incl. the frame as base64, optionally) that n8n can
  act on (save image, send notification).

The recognition/watcher code is extracted from `Hasen-Stream` and removed
there again.

## 2. Architecture & topology

```
n8n (Docker, 192.168.178.106:5678, bridge network n8n-net)
  │  HTTP GET (schedule or webhook trigger)
  ▼
RabbitRecognition (systemd, 192.168.178.106, 0.0.0.0:8011)
  │  MJPEG GET /mjpeg (same LAN)
  ▼
Hasen-Stream backend (192.168.178.135, 0.0.0.0:8000)
  │  camera Pi
  ▼
Rabbit camera (MJPEG stream)
```

Key facts (verified 2026-09-11/12):

- n8n runs in Docker on a bridge network (`n8n-net`), port published as
  `5678:5678`, no `extra_hosts` → from inside the container
  `localhost`/`127.0.0.1` reach the container, **not** the host.
- Therefore n8n must call the **host LAN IP**:
  `http://192.168.178.106:8011/recognize` (preferred, no repo change).
- Alternative: add `extra_hosts: ["host.docker.internal:host-gateway"]`
  to the n8n compose, then use
  `http://host.docker.internal:8011/recognize`.

## 3. Trigger concept (fixed preference)

- n8n triggers RabbitRecognition **via an endpoint that RabbitRecognition
  serves** — not the other way around. ✅
- The trigger endpoint is a **GET endpoint**: `GET /recognize`
  (POST also accepted for convenience). ✅
- Query params on the trigger: `method` (frame source, default = configured
  fetch method), `threshold`, `timeout`. ✅
- No auth on the endpoint (private LAN); document as known risk. ✅

## 4. HTTP API contract

| Endpoint | Purpose | Notes |
|---|---|---|
| `GET /recognize` | **n8n trigger**: fetch frame + classify | JSON response below |
| `POST /recognize` | same | convenience |
| `GET /frame` | raw JPEG of one frame | `image/jpeg`, or 502 |
| `GET /health` | liveness + active config | shows model, stream_url, fetch_method, include_image, config_file |
| `GET /` | service metadata + endpoint list | |

`GET /recognize` 200 response:

```json
{
  "rabbit": false,
  "probability": 0.108,
  "threshold": 0.5,
  "checked_at": "2026-09-12T00:27:12Z",
  "stream_url": "http://192.168.178.135:8000",
  "model": "mobilenet_v2_rabbit",
  "frame": {"width": 320, "height": 240, "jpeg_bytes": 90621},
  "image": "<base64 JPEG, only if include_image=true>"
}
```

Error handling:

| Situation | HTTP status |
|---|---|
| frame fetch failed (stream down, timeout, malformed) | 502 |
| classification failed (model error) | 500 |
| invalid query params (e.g. `method=bogus`) | 422 (FastAPI) |

## 5. Configuration concept

Precedence (highest wins):

1. **Environment variables** (e.g. systemd `EnvironmentFile=.env`)
2. **Config file** — `config.toml` at the repo root by default, override
   with `RABBIT_CONFIG` (`.toml` or `.json`)
3. **Built-in defaults**

All keys are optional; omitted keys fall back to the next layer.

Config file layout (shipped `config.toml`):

```toml
[stream]
url = "http://192.168.178.135:8000"   # base URL of Hasen-Stream backend
method = "mjpeg"                      # frame source: "mjpeg" | "save"
timeout = 20                          # seconds to wait for one frame

[model]
path = "models/mobilenet_v2_rabbit.onnx"  # relative = repo root
# threshold omitted (or null in .json) = model default 0.5
num_threads = 2                       # ONNX Runtime intra-op threads

[service]
host = "0.0.0.0"                      # HTTP API bind address
port = 8011                           # HTTP API port (n8n calls here)
log_level = "INFO"
include_image = true                  # base64 frame in /recognize payload
```

All config items:

| Field | Config file (section) | Env var | Default |
|---|---|---|---|
| stream_url | `[stream] url` | `HASEN_STREAM_URL` | `http://192.168.178.135:8000` |
| fetch_method | `[stream] method` | `RABBIT_STREAM_METHOD` | `mjpeg` |
| stream_timeout | `[stream] timeout` | `RABBIT_STREAM_TIMEOUT` | `20` |
| model_path | `[model] path` | `RABBIT_MODEL` | `models/mobilenet_v2_rabbit.onnx` |
| threshold | `[model] threshold` | `RABBIT_THRESHOLD` | `null` (model default 0.5) |
| num_threads | `[model] num_threads` | `RABBIT_NUM_THREADS` | `2` |
| host | `[service] host` | `RABBIT_HOST` | `0.0.0.0` |
| port | `[service] port` | `RABBIT_PORT` | `8011` |
| log_level | `[service] log_level` | `RABBIT_LOG_LEVEL` | `INFO` |
| include_image | `[service] include_image` | `RABBIT_INCLUDE_IMAGE` | `true` |

Flat top-level keys (field names) are accepted in the file as well —
handy for `.json` configs. `.toml` requires Python ≥ 3.11 (true on the Pi
and in this repo's venvs); `.json` works on any version.

## 6. Package layout

```
RabbitRecognition/
├── CONCEPT.md                  # this document
├── README.md                   # user-facing setup & usage docs
├── config.toml                 # default configuration file
├── .env.template               # environment overrides template
├── requirements.txt            # fastapi, uvicorn, onnxruntime, opencv-headless, numpy, requests
├── requirements-dev.txt        # + pytest (dev)
├── rabbit_recognition/
│   ├── __init__.py             # version
│   ├── config.py               # defaults < file < env, Settings dataclass
│   ├── frame_fetcher.py        # MJPEG chunk extraction, fetch_jpeg_from_mjpeg / via_save, decode
│   ├── classifier.py           # ONNX MobileNetV2, manifest-driven normalization
│   ├── service.py              # fetch + classify orchestration, RecognitionResult
│   ├── api.py                  # FastAPI app (create_app injectable), uvicorn main
│   └── check.py                # one-shot CLI (fetch, classify, print)
├── mocks/
│   └── mock_stream.py          # local stand-in for the Hasen-Stream backend
├── tests/
│   ├── test_config.py          # config layer (defaults/file/env/precedence)
│   ├── test_frame_fetcher.py   # chunk boundary + malformed stream tests
│   ├── test_classifier.py      # ONNX sanity + preprocessing
│   └── test_api.py             # endpoint contract with injected fakes
├── models/                     # mobilenet_v2_rabbit.onnx (+ .onnx.data) + manifest.json
├── train/                      # retraining + ONNX export scripts
├── n8n/
│   └── rabbit-recognition-flow.json  # importable n8n workflow
└── deploy/
    └── rabbit-recognition.service    # systemd unit
```

## 7. n8n workflow

File: `n8n/rabbit-recognition-flow.json` (import via n8n UI:
*Workflows → Import from File*). Fixed UUIDs, importable as-is.

Flow (trigger → call → evaluate → persist frame):

1. **Schedule trigger** — every 5 minutes (⏱ user-adjustable).
2. **HTTP Request** — `GET http://192.168.178.106:8011/recognize`
   (options: never fail; on failure the workflow just stops).
3. **IF** — `body.rabbit == true`.
4. **true branch**: Move Binary Data (base64 `body.image` → binary) →
   Read/Write Files (store `/home/fabi/rabbits/frames/rabbit_<timestamp>.jpg`;
   path writable by the n8n container user — adjust/bind-mount as needed).
5. **false branch**: NoOp.

Planned extensions (⬜ backlog):
- Push notification when a rabbit appears (Telegram/Email node).
- Save a small thumbnail even on "no rabbit" for auditability.
- Switch trigger from schedule to a webhook (e.g. manual/external trigger).

## 8. Deployment

- systemd unit `deploy/rabbit-recognition.service`
  (`User=fabi`, `WorkingDirectory=/home/fabi/RabbitRecognition`,
  `EnvironmentFile=.env`, `ExecStart=.../venv/bin/python -m rabbit_recognition.api`,
  `Restart=on-failure`, `RestartSec=30`).
- Install steps (Pi): `git clone` → `python3 -m venv venv` →
  `pip install -r requirements.txt` → copy `.env.template` to `.env` →
  `systemctl --user install/enable/start rabbit-recognition`.
  (Concretize as a Taskfile once the layout is final — ⬜)
- The service binds `0.0.0.0:8011` so n8n (same host, other network
  namespace) can reach it.

## 9. Testing strategy

- Unit/integration tests: `venv/bin/python -m unittest discover -s tests`
  (28 tests, all green):
  - config layer: defaults, TOML file, JSON file, env override,
    missing-file fallback, invalid method rejection
  - frame fetcher: MJPEG chunk boundaries, part headers, malformed streams,
    live mock-server fetches
  - classifier: ONNX determinism, threshold semantics, preprocessing
    normalization round-trip
  - API: endpoint contract with injected fake classifier/service
    (200/422/502/500, include_image toggle)
- E2E (manual, local): mock stream + real model + live API verified
  (`/recognize` JSON incl. base64 frame, `/health` config reporting,
  config-file-driven port/URL/include_image). ✅
- On the Pi: verify against the live camera stream (👤/🔧).

## 10. Model update path

- Retraining scripts in `train/` are carried over unchanged (🔧 revisit
  once the service is live: retrain on new frames, export ONNX, replace
  `models/`, restart service).
- Known model weakness (carried over from Hasen-Stream): trained on
  529 rabbit / 26 no-rabbit frames → class imbalance, sensitivity not yet
  validated in the field. ⬜

## 11. Status checklist

| # | Item | Status |
|---|---|---|
| 1 | Repo created, model + train dir carried over | ✅ |
| 2 | Config layer (defaults < file < env, TOML/JSON) | ✅ |
| 3 | Frame fetcher (MJPEG + save) | ✅ |
| 4 | ONNX classifier (manifest-driven) | ✅ |
| 5 | Service orchestration | ✅ |
| 6 | FastAPI app: `/recognize` (GET trigger), `/frame`, `/health`, `/` | ✅ |
| 7 | One-shot CLI `check.py` | ✅ |
| 8 | Mock stream for local testing | ✅ |
| 9 | Unit tests (28, green) | ✅ |
| 10 | Local E2E with mock + real model | ✅ |
| 11 | n8n workflow JSON (importable) | ✅ |
| 12 | systemd unit | ✅ |
| 13 | `config.toml` shipped + `.env.template` | ✅ |
| 14 | README (setup, API, n8n, deployment) | ✅ |
| 15 | Push repo to GitHub | ✅ (commit `76a8f2d`, branch `main`) |
| 16 | Deploy to Pi (clone, venv, .env, enable service) | ⬜ |
| 17 | Import n8n workflow + adjust schedule/URL (user action) | 👤 |
| 18 | Live verification against camera stream on Pi | ⬜ |
| 19 | Hasen-Stream cleanup branch (remove recognition/watcher) | ✅ (`feature/separate-rabbit-recognition-functionality` @ `da57fd0`) |
| 20 | Taskfile for Pi install (optional convenience) | ⬜ |
| 21 | n8n notification/audit extensions (backlog, §7) | ⬜ |

## 12. Open questions / risks

- Endpoint has no auth (private LAN). Acceptable? (documented)
- 5-minute schedule vs. event-based: fine for the use case; revisit if
  latency matters.
- If n8n's Docker network changes, re-check the call URL.
- Model sensitivity: get real "no rabbit" frames into training before
  trusting alerts (carried over).
