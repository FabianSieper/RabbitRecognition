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
n8n (Docker, pi4:5678, bridge network n8n-net)
  │  HTTP GET (schedule or webhook trigger)
  ▼
RabbitRecognition (rc.local + Taskfile, pi4, 0.0.0.0:8011)
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
- Therefore n8n should call the service via the resolvable hostname
  `http://pi4:8011/recognize` (preferred, no repo change).
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

1. **Environment variables** (e.g. `.env` on the Pi)
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
├── Taskfile.yml                # install / run / stop / status / rc-local-*
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
└── n8n/
    ├── README.md                       # import/setup/migration docs
    ├── rabbit-recognition-flow.json    # importable n8n workflow (frame persistence)
    ├── rabbit-recognition-telegram.json  # importable n8n workflow (Telegram + commands)
    └── rabbit-recognition-discord.json   # importable n8n workflow (Discord fixed channel)
```

## 7. n8n workflows

Three importable files (n8n UI: *Workflows → Import from File*; fixed
UUIDs, importable as-is; see [`n8n/README.md`](n8n/README.md) for setup
and migration steps):

### `rabbit-recognition-telegram.json` (recommended)

Replaces the old Hasen-Stream n8n flows (webhook "image-upload" and the
Telegram command flow). Two branches:

1. **Scheduled recognition** (top):
   1. **Schedule trigger** — every 5 minutes (user-adjustable).
   2. **HTTP Request** — `GET http://pi4:8011/recognize`
      (options: never fail; on failure the workflow just stops).
   3. **IF** — `body.rabbit == true`.
   4. **true branch**: base64 `body.image` → binary → Data Table
      `rabbit_subscriptions` → **Telegram sendPhoto** to chats with
      `images = true` (image only, no caption).
   5. **false branch**: NoOp (nothing is sent).
2. **Telegram commands** (bottom):
   1. **Telegram Trigger** (message updates, chat/user filtered).
   2. **Code** — access check against `allowedChatIds`, then
      `/stop`, `/start`, `/status`, `/chatid`, `/hilfe`; subscription
      state in Data Table `rabbit_subscriptions`.
   3. **Telegram sendText** — confirmation.

Note: activating this workflow takes over the bot's Telegram webhook,
so the old flows must be deactivated/deleted first.

### `rabbit-recognition-discord.json` (Discord fixed channel)

Discord analog of the Telegram workflow, but without user commands:
scheduled recognition and, if a rabbit is recognized, delivery of the
frame as a file upload to one fixed Discord server channel selected in
the `Bild an Discord senden` node.

### `rabbit-recognition-flow.json` (frame persistence)

1. **Schedule trigger** — every 5 minutes.
2. **HTTP Request** — `GET http://pi4:8011/recognize`
   (never fail).
3. **IF** — `body.rabbit == true`.
4. **true branch**: Move Binary Data (base64 `body.image` → binary) →
   Read/Write Files (store `/home/fabi/rabbits/frames/rabbit_<timestamp>.jpg`;
   path writable by the n8n container user — adjust/bind-mount as needed).
5. **false branch**: NoOp.

Remaining backlog (⬜):
- Save a small thumbnail even on "no rabbit" for auditability.
- Switch trigger from schedule to a webhook (e.g. manual/external trigger).

## 8. Deployment

Primary (matches the existing pi4 setup): boot autostart via
`/etc/rc.local` + Taskfile:

- `Taskfile.yml`:
  - `install` — venv + dependencies + `.env` (from template if missing)
  - `run` — start the API as a detached background process
    (`nohup … &`; pid: `run.pid`, log: `run.log`; refuses to double-start)
  - `stop` / `status` — manage the detached process via `run.pid`
  - `rc-local-check` — read-only check for the rc.local entry
  - `rc-local-add` — idempotent registration (sudo; inserts before
    `exit 0` if present, appends otherwise, no-op when already present)
- Line written (plain, same style as the other pi4 entries; `task run`
  detaches itself, so rc.local never blocks; no auto-restart after
  crash — start manually with `task run`):
  `su - fabi -c 'cd /home/fabi/RabbitRecognition && task run'`
- Pi prerequisites: go-task + Python 3.11.

The service binds `0.0.0.0:8011` so n8n (same host, other network
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
| 11 | n8n workflow JSONs (2 importable, fixed UUIDs: telegram + persistence) | ✅ |
| 12 | Boot autostart via `/etc/rc.local` (Taskfile) | ✅ |
| 13 | `config.toml` shipped + `.env.template` | ✅ |
| 14 | README (setup, API, n8n, deployment) | ✅ |
| 15 | Push repo to GitHub | ✅ (commit `76a8f2d`, branch `main`) |
| 16 | Deploy to Pi (clone, `task install`, `task rc-local-add`, verify) | ⬜ |
| 17 | Import n8n workflow + adjust schedule/URL (user action) | 👤 |
| 18 | Live verification against camera stream on Pi | ⬜ |
| 19 | Hasen-Stream cleanup branch (remove recognition/watcher) | ✅ (`feature/separate-rabbit-recognition-functionality` @ `da57fd0`) |
| 20 | Taskfile (install/run/stop/status/rc-local-check/rc-local-add) | ✅ |
| 21 | n8n notification flow (Telegram photo + /stop /start /status) | ✅ |
| 22 | n8n audit extensions (thumbnail on "no rabbit", §7 backlog) | ⬜ |
| 23 | First `task rc-local-add` + boot verification on the Pi | 👤 |

## 12. Open questions / risks

- Endpoint has no auth (private LAN). Acceptable? (documented)
- 5-minute schedule vs. event-based: fine for the use case; revisit if
  latency matters.
- If n8n's Docker network changes, re-check the call URL.
- Model sensitivity: get real "no rabbit" frames into training before
  trusting alerts (carried over).
