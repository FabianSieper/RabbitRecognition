# n8n workflows for RabbitRecognition

Two importable workflows. Both are ready for the current n8n instance
(192.168.178.106): fixed UUIDs, correct host IP, pre-filled Telegram
chat + credential ("Hasi"). Import via n8n UI: **Workflows → Import
from File**.

## `rabbit-recognition-telegram.json` — notification + control (recommended)

Replaces the old Hasen-Stream flows (webhook "image-upload" and the
Telegram command flow). It has two branches:

### Branch 1: scheduled recognition

Every 5 minutes the workflow calls the service and **only if a rabbit
is recognized** forwards the frame to Telegram — image only, no text:

```
Schedule (5 min) → HTTP GET http://192.168.178.106:8011/recognize
  → IF $json.rabbit == true
      true:  base64 `image` → binary → pause check → Telegram sendPhoto (no caption)
      false: NoOp (nothing is sent)
```

### Branch 2: Telegram commands

```
Telegram Trigger (messages) → command code → Telegram sendText (confirmation)
```

- `/stop` — pause image notifications
- `/start` — resume
- `/status` — show current state

Only your own account (chat/user `632078830`) can send commands;
everything else is ignored. Pause state is kept in workflow static
data (`telegramPaused`) and checked before every photo is sent.

### Setup (3 steps)

1. **Deactivate the old flows first** (same bot → only one workflow
   can hold the Telegram webhook): the old Hasen-Stream flows
   ("image-upload" webhook and the command flow) must be turned off or
   deleted before activating the new one.
2. **Import**: Workflows → Import from File →
   `rabbit-recognition-telegram.json`. On any instance other than the
   current one, open both Telegram nodes and re-select your Telegram
   credential.
3. **Activate** the workflow (toggle in the workflow list). Done.

### Adjustment points

- **Schedule**: interval in the first node (default 5 min).
- **Recipient**: `chatId` in "Bild an Telegram senden" (pre-filled with
  the current chat).
- **Authorized controller**: chat/user id checks in the command code
  node (and the Trigger's chat/user fields).
- **Threshold**: append `?threshold=0.7` to the HTTP node URL.
- **Pause (without Telegram)**: deactivate the workflow.

### Behavior notes

- The HTTP node has *never fail* set: if the service is down, that run
  simply stops — visible in the Execution list, no error spam.
- The base64 frame is only present when `include_image = true`
  (default in `config.toml`).
- The Telegram Trigger registers the bot's webhook when activated —
  hence step 1 (old flows off) is required.

## `rabbit-recognition-flow.json` — frame persistence

Same trigger/call/gate, but the true branch saves the frame to disk
(`/home/fabi/rabbits/frames/rabbit_<timestamp>.jpg` — path must be
writable by the n8n container user, e.g. via bind-mount) instead of
sending it to Telegram. Use both, one, or neither.

## Migration checklist

- [ ] Old Hasen-Stream flows deactivated/deleted in n8n
- [ ] New workflow imported + activated
- [ ] `/status` → `/stop` → `/start` work, and a recognized rabbit
      delivers a photo (and none while paused)
- [ ] Old camera-Pi watcher disabled:
      `sudo systemctl disable --now hasen-rabbit-watch` on 192.168.178.135