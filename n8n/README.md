# n8n workflows for RabbitRecognition

Two importable workflows. Both are ready for the current n8n instance
(192.168.178.106): fixed UUIDs, correct host IP, pre-filled Telegram
chat + credential ("Hasi"). Import via n8n UI: **Workflows → Import
from File**.

## ⚠️ Important: the service is addressed by a hardcoded IP

Both workflows call RabbitRecognition by a **hardcoded IP**
(`http://192.168.178.106:8011`). If that host gets its address from
**DHCP**, the IP can change at any time (e.g. `.106` → `.108`) and every
scheduled run then fails with *"The host is unreachable, perhaps the
server is offline"* — even though the service itself is running fine.
This is exactly what happened on 2026-09-12, when the Pi's DHCP lease
moved from `.106` to `.108` and the whole workflow went dark.

- **Recommended fix:** reserve the service host's IP in the router so it
  never changes — e.g. Fritz!Box *Heimnetz → Netze → DHCP-Server →
  Reservierungen* (add the host by MAC, pin a fixed IP). Then set that
  same IP in the HTTP node(s).
- **Don't use the bare hostname** (e.g. `pi4`) instead: on this network
  `pi4` resolves to `127.0.1.1` (localhost) and `pi4.local` is claimed by a
  different/stale device (the Pi registers itself as `pi4-2.local`), so a
  hostname is unreliable and can point at the wrong machine.
- **Symptom vs. cause:** "host is unreachable" from n8n while the service
  answers on its own IP = the workflow's IP no longer matches the host.
  Check the host's current IP first; don't restart the service.

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
            → Telegram sendPhoto (ohne Text)
      false: NoOp (nothing is sent)
```

**Foto oder nichts, ohne jeglichen Text:** An erkannte Hasen gibt es
ausschließlich Fotos — das Bild wird ohne Caption/Text und ohne n8n
Attribution gesendet. Wird ein Benutzer nicht angesprochen (`/stop`)
oder liegen gar keine Bild-Daten vor (`include_image = false`), wird
nichts gesendet — nie eine reine Textnachricht.

Each user's subscription is stored in the n8n Data Table
`rabbit_subscriptions`, one row per Telegram chat:

| Column   | Type    | Meaning                                |
|----------|---------|----------------------------------------|
| `chatId` | String  | Telegram chat id of the subscriber     |
| `images` | Boolean | `true` = receive rabbit photos         |

`/start` and `/stop` upsert the row; `/status` reads it. The photo
branch sends to all rows with `images = true`. Until a row exists,
nothing is sent.

### Branch 2: Telegram commands (per user)

```
Telegram Trigger (messages)
  → Zugriff prüfen
  → Kommandos verarbeiten
  → Befehle routen
  → Telegram sendText (confirmation)
```

- `/start` — receive photos when a rabbit is detected
- `/stop` — receive nothing (photos off)
- `/status` — show the current setting
- `/chatId` — show your own chat id; this command works even before
  the chat id is whitelisted
- `/hilfe` — command overview

Access is controlled by a dedicated code node named **`Zugriff
prüfen`**. The allowed Telegram chat ids are maintained in:

```js
const allowedChatIds = ['632078830'];
```

An empty list means "everyone is allowed". A non-empty list only lets
the listed chat ids use `/start`, `/stop`, `/status`, and `/hilfe`.
`/chatId` remains available so unknown users can obtain their own id
for whitelisting.

All Telegram text responses have n8n attribution disabled
(`appendAttribution = false`).

### Setup (4 steps, in this order)

1. **Deactivate the old flows first** (same bot → only one workflow
   can hold the Telegram webhook): the old Hasen-Stream flows
   ("image-upload" webhook and the command flow) must be turned off or
   deleted before activating the new one.
2. **Create the Data Table**: in the n8n left navigation open
   **Data Tables** and create a table named exactly
   `rabbit_subscriptions`. Add these two columns manually:
   - `chatId` — String
   - `images` — Boolean

   If `rabbit_subscriptions` already exists, open it and make sure it
   has at least those two columns. The workflow resolves the table by
   name, but it does **not** create missing columns automatically.
3. **Import**: Workflows → Import from File →
   `rabbit-recognition-telegram.json`. On any instance other than the
   current one, open the Telegram nodes and re-select your Telegram
   credential.
4. **Activate** the workflow (toggle in the workflow list). Done.

### Adjustment points

- **Schedule**: interval in the first node (default 5 min).
- **Recipients**: rows in the `rabbit_subscriptions` Data Table;
  users switch their own row by messaging the bot (commands above).
- **Authorized chat ids**: `allowedChatIds` list in the "Zugriff
  prüfen" code node (empty = everyone).
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
- If a Data Table node fails, open the execution and check whether
  `rabbit_subscriptions` exists with `chatId` (String) and `images`
  (Boolean).
- Confirmation messages for `/start` and `/stop` are generated
  explicitly in the "Abonnement bestätigen" node, so they no longer
  depend on a missing `text` field from the command code node.

## `rabbit-recognition-flow.json` — frame persistence

Same trigger/call/gate, but the true branch saves the frame to disk
(`/home/fabi/rabbits/frames/rabbit_<timestamp>.jpg` — path must be
writable by the n8n container user, e.g. via bind-mount) instead of
sending it to Telegram. Use both, one, or neither.

## Migration checklist

- [ ] Old Hasen-Stream flows deactivated/deleted in n8n
- [ ] Data Table `rabbit_subscriptions` exists with `chatId`
      (String) and `images` (Boolean)
- [ ] New workflow imported + activated (replaces the single-user
      version: after import, delete the old workflow so only one
      workflow holds the bot's webhook)
- [ ] `allowedChatIds` in "Zugriff prüfen" contains the chat ids that
      may control their subscription
- [ ] `/status`, `/start`, `/stop` work per user without n8n
      attribution text
- [ ] Second Telegram account can toggle independently; `/status`
      reflects each user's own setting
- [ ] A recognized rabbit delivers a photo (users with `/start`);
      users with `/stop` receive nothing — never text
