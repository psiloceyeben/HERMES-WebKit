# TELEGRAM — Channel Configuration

## What this does
Connects your vessel to a Telegram bot so you can receive and reply to
visitor messages from anywhere — your phone, desktop, wherever Telegram runs.

When a visitor chats through your website, you get a Telegram notification.
You can reply directly from Telegram and the response appears on the site.

---

## Setup (5 minutes)

### 1. Create a bot
Open Telegram and message [@BotFather](https://t.me/botfather):

```
/newbot
```

Follow the prompts. BotFather gives you a **bot token** like:
```
7123456789:AAHdqTWE3vLtHJb_xxxxxxxxxxxxxxxxxxx
```

### 2. Get your Telegram user ID
Message [@userinfobot](https://t.me/userinfobot) — it replies with your numeric ID.

### 3. Add to .env
```bash
TELEGRAM_BOT_TOKEN=7123456789:AAHdqTWE3vLtHJb_xxxxxxxxxxxxxxxxxxx
TELEGRAM_ALLOWED_IDS=123456789
```

Multiple allowed IDs (for a team): `TELEGRAM_ALLOWED_IDS=123456789,987654321`

### 4. Restart the bridge
```bash
systemctl restart hermes
```

---

## How it works

The bridge runs a lightweight webhook-style polling loop alongside the main server.
When a visitor submits a chat message on your site, the bridge:

1. Routes it through the Tree (HECATE → nodes → MALKUTH)
2. Sends the visitor's message + the vessel's response to your Telegram
3. Awaits your reply (optional — vessel already responded)

If you reply in Telegram within the **reply window** (configurable, default 60s),
your reply is appended to the conversation and sent back to the visitor.

This means you can inject your own voice into live conversations
when you want to — without replacing the vessel for routine exchanges.

---

## Reply format

Just reply normally in Telegram. The bridge strips any leading commands.

To send a private note to yourself (not forwarded to visitor):
```
//note This visitor seems like a serious lead
```

Any message starting with `//` is logged but not forwarded.

---

## Security

- Only `TELEGRAM_ALLOWED_IDS` can send commands to the bridge
- The bot does not respond to any other Telegram users
- Visitor messages are never stored beyond the active session

---

## Disabling

Remove `TELEGRAM_BOT_TOKEN` from `.env` and restart. The channel silently deactivates.
