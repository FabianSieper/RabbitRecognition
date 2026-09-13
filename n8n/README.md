# n8n workflows for RabbitRecognition

Three importable workflows. All are ready for the current n8n instance
(`pi4`) with fixed UUIDs and the service URL `http://pi4:8011`; the
Telegram workflow additionally ships with the pre-filled Telegram
credential ("Hasi"). Import via n8n UI: **Workflows → Import from
File**.

## ⚠️ Important: the service is addressed by hostname `pi4`

All three workflows call RabbitRecognition via the resolvable hostname
`http://pi4:8011`. This survives DHCP address changes because the
workflow does not hardcode the Pi's current LAN IP. The hostname must
resolve correctly from the n8n container to the RabbitRecognition host
(router DNS, mDNS, Docker network alias, or `extra_hosts`).

If `pi4` resolves to `127.0.1.1`, a stale `pi4.local`, or another
machine, fix name resolution before importing/activating:

- **Preferred:** make the router/mDNS answer for `pi4` point at the
  RabbitRecognition host.
- **Alternative:** add a Docker network alias or `extra_hosts` entry in
  the n8n compose so the container resolves `pi4` to the host IP.
- **Symptom vs. cause:** `ENOTFOUND` or "host unreachable" from n8n
  while the service answers on the host = wrong or missing name
  resolution for `pi4` in the container.

## `rabbit-recognition-telegram.json` — notification + control (recommended)

Replaces the old Hasen-Stream flows (webhook "image-upload" and the
Telegram command flow). It has two branches:

### Branch 1: scheduled recognition (multi-user)

Every 5 minutes the workflow calls the service and **only if a rabbit
is recognized** notifies **all subscribed Telegram users**:

```
Schedule (5 min) → HTTP GET http://pi4:8011/recognize
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

## `rabbit-recognition-discord.json` — Discord fixed-channel notification

Discord analog of the Telegram workflow, but **without user commands**.
Every 5 minutes the workflow calls RabbitRecognition and, **only if a
rabbit is recognized**, sends the frame as a file upload to one fixed
Discord server channel selected in the `Bild an Discord senden` node.

```
Schedule (5 min) → HTTP GET http://pi4:8011/recognize
  → IF $json.rabbit == true
      true:  base64 `image` → binary → Discord send to fixed channel
      false: NoOp (nothing is sent)
```

### Setup

1. Create/select a standard **Discord API** credential (Bot Token).
2. Import `rabbit-recognition-discord.json`.
3. In `Bild an Discord senden`, select the Discord API credential and
   set the fixed `guildId` and `channelId` of the target channel.
4. Activate the workflow.

The workflow intentionally has no `Discord Kommandos` trigger, no Data
Table subscription nodes, and no `/start`, `/stop`, `/status`,
`/chatId`, or `/hilfe` handling. Images are always sent to the fixed
channel; users cannot currently control that via Discord.

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

Discord workflow (optional):

- [ ] Standard Discord API credential created
- [ ] `Bild an Discord senden` has the fixed `guildId` and `channelId`
- [ ] Workflow activated; a recognized rabbit delivers a photo to the
      fixed channel
