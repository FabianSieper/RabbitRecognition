# n8n workflows for RabbitRecognition

Two importable workflows. Both are ready for the current n8n instance
(192.168.178.106): fixed UUIDs, correct host IP, pre-filled Telegram
chat + credential ("Hasi"). Import via n8n UI: **Workflows → Import
from File**.

## `rabbit-recognition-telegram.json` — notification + control (recommended)

Replaces the old Hasen-Stream flows (webhook "image-upload" and the
Telegram command flow). It has two branches:

### Branch 1: scheduled recognition (multi-user)

Every 5 minutes the workflow calls the service and **only if a rabbit
is recognized** notifies **all subscribed Telegram users**:

```
Schedule (5 min) → HTTP GET http://192.168.178.106:8011/recognize
  → IF $json.rabbit == true
      true:  base64 `image` → binary → one item per user with images on
            → Telegram sendPhoto (caption = timestamp text)
      false: NoOp (nothing is sent)
```

**Foto oder nichts:** An erkannte Hasen gibt es ausschließlich Fotos.
Wird ein Benutzer nicht angesprochen (`/kein-bild`) oder liegen gar
keine Bild-Daten vor (`include_image = false`), wird nichts gesendet —
nie eine reine Textnachricht.

Each user's setting (`images`) is stored per chat id in workflow
static data: `state.users['user-<chatId>'] = { images }`. It is
created on first contact with the bot (default: images on) and
changed via the commands below. Until someone has contacted the
bot, nothing is sent.

### Branch 2: Telegram commands (per user)

```
Telegram Trigger (messages, any user) → command code → Telegram sendText (confirmation)
```

- `/bild` — receive photos when a rabbit is detected
- `/kein-bild` — receive nothing (photos off)
- `/start` — alias for `/bild`
- `/stop` — alias for `/kein-bild`
- `/status` — show the current setting
- `/hilfe` — command overview

Every user manages only their own subscription. An optional allow
list (`ALLOWED` in the command code node, empty = everyone) can
restrict which Telegram ids may use the bot.

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
- **Recipients**: per-user photo settings in workflow static data;
  users switch them by messaging the bot (commands above).
- **Authorized controllers**: `ALLOWED` list in the "Kommandos
  verarbeiten" code node (empty = everyone).
- **Threshold**: append `?threshold=0.7` to the HTTP node URL.
- **Pause (without Telegram)**: deactivate the workflow.

### Behavior notes

- The HTTP node has *never fail* set: if the service is down, that run
  simply stops — visible in the Execution list, no error spam.
- The base64 frame is only present when `include_image = true`
  (default in `config.toml`). Without it, a detected rabbit triggers
  no message at all — never a text-only fallback.
- The Telegram Trigger registers the bot's webhook when activated —
  hence step 1 (old flows off) is required.

## `rabbit-recognition-flow.json` — frame persistence

Same trigger/call/gate, but the true branch saves the frame to disk
(`/home/fabi/rabbits/frames/rabbit_<timestamp>.jpg` — path must be
writable by the n8n container user, e.g. via bind-mount) instead of
sending it to Telegram. Use both, one, or neither.

## Migration checklist

- [ ] Old Hasen-Stream flows deactivated/deleted in n8n
- [ ] New workflow imported + activated (replaces the single-user
      version: after import, delete the old workflow so only one
      workflow holds the bot's webhook)
- [ ] `/status`, `/bild`, `/kein-bild` work per user
- [ ] Second Telegram account can toggle independently; `/status`
      reflects each user's own setting
- [ ] A recognized rabbit delivers a photo (users with `/bild`);
      users with `/kein-bild` receive nothing — never text