# RabbitRecognition

Standalone HTTP service that fetches a live camera frame from the **Hasen-Stream**
backend (Raspberry Pi) and classifies it for rabbits using a fine-tuned
**MobileNetV2 ONNX** model. It is meant to be **triggered by n8n** (which runs in
Docker on the same Pi), e.g. on a schedule or via webhook.

```
 n8n (Docker, n8n Pi 192.168.178.106)
   │  HTTP Request node: GET http://192.168.178.106:8011/recognize
   ▼
 RabbitRecognition (host process, same Pi, port 8011)
   │  frame fetch: GET http://192.168.178.135:8000/mjpeg
   ▼
  Hasen-Stream backend (camera Pi 192.168.178.135, MJPEG of the hutch)
```

All boxes are in the same LAN; no tunneling involved.

Design and status of everything planned lives in
[`CONCEPT.md`](CONCEPT.md) — the central document.

## Repository layout

```
.
├── CONCEPT.md               # central design & status document
├── Taskfile.yml             # install / run / stop / status / rc-local-*
├── config.toml              # default configuration file (see below)
├── .env.template            # env-var overrides template
├── rabbit_recognition/      # Python package
│   ├── api.py               # FastAPI app (create_app(), /recognize, /frame, /health)
│   ├── service.py           # RecognitionService + RecognitionResult
│   ├── classifier.py        # RabbitClassifier (ONNX, manifest-driven)
│   ├── frame_fetcher.py     # MJPEG / image-save fetchers
│   ├── check.py             # one-shot CLI
│   └── config.py            # config layer (defaults < config file < env)
├── mocks/                   # mock of the Hasen-Stream backend (for local testing)
├── n8n/                     # importable n8n workflows (see n8n/README.md)
├── models/                  # mobilenet_v2_rabbit.onnx (+ .onnx.data) and manifest.json
├── train/                   # retraining scripts, checkpoint, dataset info
└── tests/                   # unit + integration tests (unittest)
```

## Quick start (local / any Linux host)

```bash
python3.11 -m venv venv
./venv/bin/pip install -r requirements.txt

# point at the camera Pi's backend (also the default):
export HASEN_STREAM_URL="http://192.168.178.135:8000"

# one-shot check
./venv/bin/python -m rabbit_recognition.check

# start the HTTP service
./venv/bin/python -m rabbit_recognition.api
# then: curl http://127.0.0.1:8011/recognize
```

### Configuration

Everything is configurable via a **config file** and/or **environment
variables**. Precedence (highest wins):

1. environment variables
2. config file (`config.toml` at the repo root by default; point
   `RABBIT_CONFIG` at any other `.toml` or `.json` file)
3. built-in defaults

The shipped `config.toml` (all keys optional):

```toml
[stream]
url = "http://192.168.178.135:8000"   # Hasen-Stream backend (camera Pi)
method = "mjpeg"                      # frame source: "mjpeg" | "save"
timeout = 20                          # seconds to wait for one frame

[model]
path = "models/mobilenet_v2_rabbit.onnx"
# threshold omitted (or null in a .json config) → model default 0.5
num_threads = 2

[service]
host = "0.0.0.0"                      # bind address of this service
port = 8011                           # port n8n calls
log_level = "INFO"
include_image = true                  # base64 frame in /recognize payload
```

Environment overrides (e.g. `.env` on the Pi):

| Variable               | Config key            | Default                              |
| ---------------------- | --------------------- | ------------------------------------ |
| `HASEN_STREAM_URL`     | `[stream] url`        | `http://192.168.178.135:8000`        |
| `RABBIT_STREAM_METHOD` | `[stream] method`     | `mjpeg`                              |
| `RABBIT_STREAM_TIMEOUT`| `[stream] timeout`    | `20` (s)                             |
| `RABBIT_MODEL`         | `[model] path`        | `models/mobilenet_v2_rabbit.onnx`    |
| `RABBIT_THRESHOLD`     | `[model] threshold`   | *(empty)* → manifest default (0.5)   |
| `RABBIT_NUM_THREADS`   | `[model] num_threads` | `2`                                  |
| `RABBIT_HOST`          | `[service] host`      | `0.0.0.0`                            |
| `RABBIT_PORT`          | `[service] port`      | `8011`                               |
| `RABBIT_LOG_LEVEL`     | `[service] log_level` | `INFO`                               |
| `RABBIT_INCLUDE_IMAGE` | `[service] include_image` | `true`                           |
| `RABBIT_CONFIG`        | —                     | repo root `config.toml`              |

Note: `.toml` parsing uses the Python ≥ 3.11 stdlib (`tomllib`); on older
interpreters use a `.json` file (flat keys or the same sections).

### HTTP API

- `GET /recognize` — **the n8n trigger endpoint**: fetch one frame and
  classify it. (POST also accepted.)
  Query params: `method=mjpeg|save` (default = configured fetch method),
  `threshold=0..1` (optional override), `timeout=<s>` (optional).

  `200` response:

  ```json
  {
    "rabbit": true,
    "probability": 0.93,
    "threshold": 0.5,
    "checked_at": "2026-09-12T14:32:11.482Z",
    "stream_url": "http://192.168.178.135:8000",
    "model": "mobilenet_v2_rabbit",
    "frame": {"width": 1640, "height": 1232, "jpeg_bytes": 951234},
    "image": "<base64 JPEG of the classified frame>"
  }
  ```

  `502` if the frame fetch fails (stream/backend unreachable), `500` if the
  classification itself fails, `422` for an invalid `method`.

- `GET /frame` — raw JPEG (`image/jpeg`) of the fetched frame; `502` on failure.
- `GET /health` — model name, default threshold, stream URL, fetch method,
  `include_image`, and the loaded config file.
- `GET /` — service info.

## n8n integration

n8n runs in Docker (`n8nio/n8n`, bridge network `n8n-net`) on the **same Pi** as
RabbitRecognition (host process bound to `0.0.0.0:8011`).

### How n8n should call the service

| URL used in the HTTP Request node      | Works? | Notes                                                                 |
| -------------------------------------- | ------ | --------------------------------------------------------------------- |
| `http://192.168.178.106:8011/recognize` | ✅ recommended | n8n container → host via bridge NAT. No n8n repo change needed. Breaks only if the Pi's IP changes (use a fixed IP/DHCP reservation). |
| `http://host.docker.internal:8011/recognize` | ⚠️ only with one line added | On Linux, `host.docker.internal` is **not** resolved by default (unlike Docker Desktop). Add to the `n8n` service in your n8n repo's `docker-compose.yml`: `extra_hosts: ["host.docker.internal:host-gateway"]`, then `docker compose up -d n8n`. Survives IP changes. |
| `http://localhost:8011` / `127.0.0.1:8011` | ❌ no | These refer to the n8n container itself, not the host. |

Recommendation: use the LAN IP (no repo change); if you want IP-change
resilience, add the `extra_hosts` line to the n8n compose and use
`host.docker.internal`.

### Importing a workflow

Both files in [`n8n/`](n8n/README.md) are importable via n8n UI
(Workflows → Import from File); see the [n8n README](n8n/README.md) for
setup steps and adjustment points:

- **`rabbit-recognition-telegram.json`** (recommended): every 5 min →
  `GET http://192.168.178.106:8011/recognize` → **only if `rabbit ==
  true`**: send the frame to Telegram (image only, no caption); plus a
  Telegram command branch (`/stop`, `/start`, `/status`) to pause
  notifications. Replaces the old Hasen-Stream flows; Telegram
  chat/credential are pre-filled for the current instance.
- **`rabbit-recognition-flow.json`**: same trigger/call/gate, true
  branch saves the frame to
  `/home/fabi/rabbits/frames/rabbit_<timestamp>.jpg` (path must be
  writable by the n8n container user, e.g. via bind-mount).

Both flows are *inactive* by default — activate after import.

## Deploying on the Pi (rc.local, same style as the other projects there)

The service starts at boot via `/etc/rc.local` — the same pattern as the
other `fabi` projects on that Pi
(`su - fabi -c 'cd /home/fabi/<repo> && task run'`). `Taskfile.yml`
provides everything, including the rc.local registration:

```bash
# on the n8n Pi (user fabi)
git clone git@github.com:FabianSieper/RabbitRecognition.git
cd RabbitRecognition

task install        # venv + dependencies + .env (from template)
task run            # start the service (detached; log: run.log, pid: run.pid)
task status         # is it running?
task stop           # stop the detached service

task rc-local-check # check whether it is already in /etc/rc.local
task rc-local-add   # register for boot (idempotent; asks for sudo once)

# after a reboot (or start manually via `task run`):
curl http://127.0.0.1:8011/health
```

`task rc-local-add` writes exactly this line (inserted before an
`exit 0` if present, appended otherwise) and is a no-op when it already
exists:

```
su - fabi -c 'cd /home/fabi/RabbitRecognition && task run'
```

`task run` detaches itself: it starts the API with `nohup … &` and
records `run.pid`, so a plain rc.local line is enough (same style as
the other entries there). Starting it a second time is refused
(`task stop` first).

```bash
tail -f run.log    # service log
```

Note: rc.local does not restart the service after a crash — in that
case start it manually with `task run`.

Prerequisites on the Pi: `task` (go-task) and Python 3.11.

If port 8011 ever collides with an n8n exposed port, change `RABBIT_PORT`.

## Tests

```bash
./venv/bin/pip install -r requirements-dev.txt
./venv/bin/python -m unittest discover -s tests -v
```

28 tests cover: the config layer (defaults, TOML/JSON files, env override
precedence), frame fetcher (unit + integration against the mock stream,
including chunking regressions), classifier sanity (real ONNX model,
metadata, normalization, determinism, threshold semantics), and the API
(injected fake classifier/service — no network needed).

## Local end-to-end without the camera Pi

```bash
# terminal 1: mock backend serving frames from some image directory
./venv/bin/python -m mocks.mock_stream --images /path/to/frames --port 8000

# terminal 2: service pointed at the mock
HASEN_STREAM_URL="http://127.0.0.1:8000" ./venv/bin/python -m rabbit_recognition.check
```

## Retraining the model

See `train/` (`train_final.py`, `eval_onnx.py`): the fine-tuning pipeline
(FastImageNet fine-tune of MobileNetV2 → ONNX export), the checkpoint and
dataset info (529 rabbit / 26 no-rabbit frames, threshold 0.5) live there.

## Troubleshooting

- **502 from /recognize** — the camera Pi's backend is unreachable from this
  host (wrong `HASEN_STREAM_URL`, firewall, or backend down). Check with
  `curl http://192.168.178.135:8000/mjpeg | head -c 100`.
- **n8n: connection refused / ENOTFOUND** — see the table above; `localhost`
  is wrong inside the container.
- **Slow first response** — the ONNX model is loaded at service start; a cold
  start on the Pi takes a couple of seconds.
- **High probability for empty hutch** — lower `RABBIT_THRESHOLD`; the model
  was trained on an imbalanced set, so review `manifest.json` defaults.
