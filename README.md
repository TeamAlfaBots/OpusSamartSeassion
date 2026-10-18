# Smart String Session Generator Bot

A Telegram bot that creates a Telegram client session string for the account owner through the official QR login and sends it back in the same private chat.

## Features

- `/start` menu with configurable start image and links
- Telethon and Pyrogram session strings (one QR login, two output formats)
- QR auto-refreshes before it expires (Telegram QR tokens live ~30s)
- Optional two-step verification (2FA) support, **off by default**
- One active login per user, per-user rate limit, `/cancel` and a Cancel button
- Private-chat-only flow, nothing stored on disk or in a database
- `/health` endpoint for Render
- Gram.JS / mtcute / MTKruto: shown as "coming soon" (they need a JS runtime)

## How it works

Login runs once through Telethon's QR flow. The Pyrogram string is built from the same MTProto auth key
(`dc_id | api_id | test_mode | auth_key | user_id | is_bot`, base64url), so both formats come from one login path.
The client is only disconnected afterwards, never logged out, so the session stays valid.

## Configure locally

1. Copy `.env.example` to `.env` and set `API_ID`, `API_HASH`, `BOT_TOKEN`.
2. `pip install -r requirements.txt`
3. `python main.py`  (health: `http://localhost:8080/health`)

| Variable | Default | Meaning |
| --- | --- | --- |
| `ENABLE_2FA` | `false` | Accept the user's cloud password in chat for 2FA accounts |
| `QR_TOTAL_TIMEOUT` | `180` | Max seconds for a whole login attempt |
| `QR_REFRESH_SECONDS` | `25` | How often the QR is regenerated |
| `PASSWORD_TIMEOUT` | `120` | Seconds to wait for the 2FA password |
| `MAX_ATTEMPTS` / `ATTEMPT_WINDOW` | `5` / `600` | Logins allowed per user per window (sec) |

## Deploy on Render

1. Push to a Git repo, create a Blueprint from `render.yaml` (or a Docker web service).
2. Set `API_ID`, `API_HASH`, `BOT_TOKEN` as secrets.
3. The free plan sleeps after ~15 min without inbound HTTP; `/health` alone does not prevent it. Ping it with an external uptime monitor or use a paid plan.

## Security & trust

- A session string is a bearer credential for the whole account. Whoever runs this bot technically sees every string pass through memory. Be transparent with users, never log or store them, and never add analytics around them.
- With `ENABLE_2FA=true` the bot also receives users' cloud passwords (deleted instantly, never stored/logged). Only enable it if users trust you, and say so clearly.
- Sessions are created with your `API_ID`; abuse can get that API ID limited by Telegram.
- Tell users to save the string privately, delete the chat message, and terminate the session in Settings → Devices if exposed.
- Never run with DEBUG logging in production.
- Use only for your own account or accounts you are explicitly authorised to access.

## Notes

- Untested against live Telegram in the build environment: run one full login with a test account (with and without 2FA) before going public.
- Telethon's QR API and the Pyrogram string layout are what this code relies on; if you pin a very different Pyrofork version, verify the string imports correctly.
