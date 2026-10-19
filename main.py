import asyncio
import logging
import os
import signal
import sys
from collections import Counter

import aiohttp
from aiohttp import web
from pyrogram import Client, filters

from bot import flow
from bot.config import Config
from bot.keyboards import library_keyboard, start_keyboard
from bot.logger import GroupLogHandler, gl

# Everything goes to stdout so Render's live log shows it immediately.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
    force=True,
)
logging.getLogger("aiohttp.access").setLevel(logging.WARNING)  # hide health-check spam
logging.getLogger().addHandler(GroupLogHandler(gl))            # errors -> logger group
# Pyrogram/Telethon can be chatty; never run at DEBUG in production (QR URLs, tokens).
log = logging.getLogger("smart-session-bot")

Config.validate()

app = Client(
    "smart_string_bot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN,
    in_memory=True,
)

# ---------------------------------------------------------------- health server
web_app = web.Application()


async def health(_request):
    return web.json_response({"status": "ok", "service": "Smart String Session Generator"})


web_app.router.add_get("/", health)
web_app.router.add_get("/health", health)


async def start_web():
    runner = web.AppRunner(web_app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", Config.PORT).start()
    log.info("Health server listening on port %s", Config.PORT)
    return runner


# ------------------------------------------------------------ startup checks
def _update_kind(update):
    """Summarise a Bot API update WITHOUT its text (e.g. 'private:/start')."""
    msg = update.get("message") or update.get("edited_message")
    if msg:
        text = msg.get("text") or ""
        kind = text.split()[0][:32] if text.startswith("/") else ("text" if text else "other")
        return f"{(msg.get('chat') or {}).get('type', '?')}:{kind}"
    for key in update:
        if key != "update_id":
            return key
    return "other"


async def bot_api_checks():
    """Returns (webhook_status, queue_status) for the start report.

    * A webhook on this token can stop MTProto updates -> check and optionally remove it.
    * getUpdates (read-only, no offset) shows what Telegram has queued for this bot, which
      tells us whether people's /start messages are reaching the bot at all.
    """
    base = f"https://api.telegram.org/bot{Config.BOT_TOKEN}"  # never log this URL
    webhook, queue = "check failed", "n/a"
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{base}/getWebhookInfo") as resp:
                info = (await resp.json()).get("result") or {}
            if not info.get("url"):
                webhook = "none ✅"
            else:
                log.warning(
                    "A webhook is set for this bot token. It can stop updates from reaching "
                    "this bot (/start gets no reply). last_error=%s",
                    info.get("last_error_message"),
                )
                if not Config.AUTO_DELETE_WEBHOOK:
                    webhook = "SET ⚠️ (AUTO_DELETE_WEBHOOK=false)"
                else:
                    async with session.post(f"{base}/deleteWebhook") as resp:
                        ok = bool((await resp.json()).get("ok"))
                    log.warning("Webhook removal %s", "succeeded" if ok else "FAILED")
                    webhook = "was set → removed ✅" if ok else "set, removal FAILED ⚠️"

            async with session.get(f"{base}/getUpdates", params={"limit": 20, "timeout": 0}) as resp:
                data = await resp.json()
            if data.get("ok"):
                kinds = Counter(_update_kind(u) for u in data.get("result", []))
                queue = ", ".join(f"{k} x{v}" for k, v in kinds.items()) or "empty"
            else:
                queue = f"unavailable ({str(data.get('description'))[:60]})"
    except Exception as exc:
        log.warning("Bot API check failed (%s)", type(exc).__name__)
    log.info("Webhook: %s | Telegram-side queue: %s", webhook, queue)
    return webhook, queue


def log_config_summary():
    """Print what the bot actually read from the environment (names/ids only, no secrets)."""
    for note in Config.problems():
        log.warning("CONFIG: %s", note)
    log.info(
        "Config: LOG_GROUP_ID=%s | 2FA=%s | button_styles=%s | env names containing 'LOG': %s",
        Config.LOG_GROUP_ID or "not set",
        Config.ENABLE_2FA,
        Config.BUTTON_STYLES,
        sorted(k for k in os.environ if "LOG" in k.upper()) or "none",
    )


# ------------------------------------------------------------------ watchdog
WATCHDOG_INTERVAL = 20      # seconds between checks
WATCHDOG_MAX_RESTARTS = 2   # automatic client restarts before giving up


async def _latest_telegram_update_id():
    """Newest update id Telegram holds for this bot (0 = none, None = unknown)."""
    base = f"https://api.telegram.org/bot{Config.BOT_TOKEN}"  # never log this URL
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(f"{base}/getUpdates", params={"offset": -1, "limit": 1, "timeout": 0}) as resp:
            data = await resp.json()
    if not data.get("ok"):
        return None
    result = data.get("result") or []
    return result[-1]["update_id"] if result else 0


async def watch_updates():
    """Detects 'Telegram has new updates for the bot but Pyrogram received none'.

    Telegram's own queue (Bot API view) and our raw-update counter are compared every
    interval. First mismatch -> error report; second in a row -> restart the client.
    """
    last_id = None
    try:
        last_id = await _latest_telegram_update_id()
    except Exception:
        pass
    last_raw = STATS["raw"]
    strikes = restarts = 0
    while True:
        await asyncio.sleep(WATCHDOG_INTERVAL)
        try:
            tg_id = await _latest_telegram_update_id()
        except Exception as exc:
            log.info("Watchdog check skipped (%s)", type(exc).__name__)
            continue
        if tg_id is None:
            continue
        received = STATS["raw"] - last_raw
        new_at_telegram = last_id is not None and tg_id > last_id
        last_id, last_raw = tg_id, STATS["raw"]

        if not (new_at_telegram and received == 0):
            strikes = 0
            continue

        strikes += 1
        log.error(
            "Telegram has new updates for this bot but this Pyrogram session received none "
            "(strike %s). Messages are NOT reaching the bot.",
            strikes,
        )
        if strikes >= 2 and restarts < WATCHDOG_MAX_RESTARTS:
            restarts += 1
            strikes = 0
            log.warning("Restarting the Pyrogram client (attempt %s/%s)", restarts, WATCHDOG_MAX_RESTARTS)
            try:
                await app.restart()
                last_raw = STATS["raw"]
                log.info("Client restarted")
            except Exception as exc:
                log.error("Client restart failed (%s)", type(exc).__name__)


def _loop_exception_handler(_loop, context):
    log.error("Unhandled asyncio error: %s", context.get("message", ""), exc_info=context.get("exception"))


# ------------------------------------------------------------------- helpers
def is_private_callback(query):
    return bool(query.message and query.message.chat and str(query.message.chat.type).lower().endswith("private"))


def welcome_text(name):
    return (
        f"Hey {name}! 👋\n\n"
        "Welcome to **Smart String Session Generator** — a simple tool to create a "
        "Telegram client session for your own account using your chosen library.\n\n"
        "Choose a library, scan the official Telegram QR login from your Telegram app, "
        "and receive your session string here in this private chat.\n\n"
        "🔐 **Security:** A session string can grant access to your account. Never share it "
        "with anyone, and revoke the session in Telegram Settings → Devices if exposed."
    )


# ----------------------------------------------- raw update counter (diagnostics)
STATS = {"raw": 0}


@app.on_raw_update(group=-200)
async def count_raw_updates(_, update, users, chats):
    """Counts every update Telegram pushes to this session (type names only)."""
    STATS["raw"] += 1
    n = STATS["raw"]
    if n <= 30 or n % 25 == 0:
        log.info("Raw update from Telegram: %s (total %s)", type(update).__name__, n)


# ------------------------------------------------- incoming-update diagnostics
# group=-100 runs before every other handler and never stops propagation. It logs
# only the kind of update (never message text, so passwords can't leak).
@app.on_message(group=-100)
async def log_incoming_message(_, message):
    user = message.from_user
    text = message.text or ""
    kind = text.split()[0][:32] if text.startswith("/") else ("text" if text else "other")
    chat_type = getattr(getattr(message.chat, "type", None), "name", "?")
    log.info("Incoming message: chat=%s user=%s kind=%s", chat_type, user.id if user else "?", kind)


@app.on_callback_query(group=-100)
async def log_incoming_callback(_, query):
    log.info("Incoming callback: data=%s user=%s", str(query.data)[:40], query.from_user.id if query.from_user else "?")


# ------------------------------------------------------------------ handlers
@app.on_message(filters.command("start") & filters.private)
async def start_handler(_, message):
    name = message.from_user.first_name if message.from_user else "there"
    text = welcome_text(name)
    if Config.START_IMG_URL:
        try:
            await message.reply_photo(Config.START_IMG_URL, caption=text, reply_markup=start_keyboard())
            return
        except Exception as exc:
            log.warning("START_IMG_URL failed (%s); falling back to text", type(exc).__name__)
    await message.reply_text(text, reply_markup=start_keyboard(), disable_web_page_preview=True)


@app.on_message(filters.command("cancel") & filters.private)
async def cancel_command(_, message):
    if message.from_user and flow.cancel(message.from_user.id):
        return  # the flow itself reports "Login cancelled"
    await message.reply_text("Nothing to cancel.")


async def _waiting_for_password(_, __, message):
    user = message.from_user
    text = message.text or ""
    return bool(user and user.id in flow.password_waiters and not text.startswith("/"))


@app.on_message(filters.private & filters.text & filters.create(_waiting_for_password), group=-1)
async def password_handler(_, message):
    fut = flow.password_waiters.get(message.from_user.id)
    password = message.text
    try:
        await message.delete()  # remove the password from the chat right away
    except Exception:
        pass
    if fut and not fut.done():
        fut.set_result(password)  # never logged
    message.stop_propagation()


@app.on_callback_query(filters.regex(r"^generate$"))
async def generate_menu(_, query):
    if not is_private_callback(query):
        await query.answer("Please use this bot in a private chat.", show_alert=True)
        return
    await query.message.edit_text(
        "**Which library do you use?**\n\nSelect the client library for your own Telegram account.",
        reply_markup=library_keyboard(),
    )
    await query.answer()


@app.on_callback_query(filters.regex(r"^back_start$"))
async def back_start(_, query):
    name = query.from_user.first_name if query.from_user else "there"
    text = (
        f"Hey {name}! 👋\n\n"
        "Welcome to **Smart String Session Generator** — generate a Telegram client session "
        "for your own account, then keep it private and secure."
    )
    try:
        await query.message.edit_text(text, reply_markup=start_keyboard())
    except Exception:
        pass
    await query.answer()


@app.on_callback_query(filters.regex(r"^close$"))
async def close_handler(_, query):
    try:
        await query.message.delete()
    except Exception:
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    await query.answer("Closed")


@app.on_callback_query(filters.regex(r"^soon:(.+)$"))
async def soon_handler(_, query):
    library = query.matches[0].group(1)
    await query.answer(
        f"{library} needs its own JavaScript runtime — coming soon. Use Telethon or Pyrogram for now.",
        show_alert=True,
    )


@app.on_callback_query(filters.regex(r"^library:(Telethon|Pyrogram)$"))
async def library_handler(client, query):
    if not is_private_callback(query):
        await query.answer("Please use this bot in a private chat.", show_alert=True)
        return
    user_id = query.from_user.id
    wait = flow.limiter.check(user_id)
    if wait:
        await query.answer(f"Too many attempts. Try again in {wait}s.", show_alert=True)
        return
    flow.limiter.hit(user_id)
    await query.answer()
    flow.start(client, query.message.chat.id, user_id, query.matches[0].group(1))


@app.on_callback_query(filters.regex(r"^cancel_login$"))
async def cancel_login(_, query):
    if flow.cancel(query.from_user.id):
        await query.answer("Cancelled")
    else:
        await query.answer("No active login.")
        try:
            await query.message.delete()
        except Exception:
            pass


# ---------------------------------------------------------------------- main
async def main():
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(_loop_exception_handler)

    # Render sends SIGTERM on redeploy: handle it so the "stopped" report can go out.
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    log_config_summary()
    watchdog = None
    runner = await start_web()
    try:
        await app.start()
        me = await app.get_me()
        gl.attach(app, loop)
        log.info("Bot started as @%s (id %s)", me.username, me.id)

        webhook, queue = await bot_api_checks()
        await gl.report_start(
            me,
            {
                "🌐 Webhook": webhook,
                "📥 Telegram queue": queue,
                "🔐 2FA": "enabled" if Config.ENABLE_2FA else "disabled",
                "🎨 Button styles": "on" if Config.BUTTON_STYLES else "off",
                "📡 Health port": Config.PORT,
            },
        )
        watchdog = asyncio.create_task(watch_updates())
        log.info(
            "Ready. Send /start to the bot now: you should see 'Raw update from Telegram' "
            "followed by 'Incoming message'."
        )
        await stop.wait()
        log.info("Shutdown signal received")
    finally:
        if watchdog:
            watchdog.cancel()
        try:
            await gl.report_stop()
        except Exception:
            pass
        await flow.shutdown()
        try:
            await app.stop()
        except Exception:
            pass
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception:
        log.critical("Fatal error, bot stopped", exc_info=True)
        sys.exit(1)
        
