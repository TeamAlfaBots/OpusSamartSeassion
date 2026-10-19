import asyncio
import html
import logging
import os
import signal
import sys
import time

from bot import flow
from bot.api import BotAPI, TelegramAPIError
from bot.config import Config
from bot.health import start_health_server
from bot.keyboards import library_keyboard, start_keyboard
from bot.logger import GroupLogHandler, gl

# Everything goes to stdout so Render's live log shows it immediately.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
    force=True,
)
logging.getLogger().addHandler(GroupLogHandler(gl))  # errors -> logger group
log = logging.getLogger("smart-session-bot")

Config.validate()

POLL_TIMEOUT = 25            # seconds Telegram holds each getUpdates request open
BOT = {"username": ""}       # filled at startup
_tasks = set()


# ------------------------------------------------------------------- helpers
def welcome_text(name):
    return (
        f"Hey {html.escape(name)}! 👋\n\n"
        "Welcome to <b>Smart String Session Generator</b> — a simple tool to create a "
        "Telegram client session for your own account using your chosen library.\n\n"
        "Choose a library, scan the official Telegram QR login from your Telegram app, "
        "and receive your session string here in this private chat.\n\n"
        "🔐 <b>Security:</b> A session string can grant access to your account. Never share it "
        "with anyone, and revoke the session in Telegram Settings → Devices if exposed."
    )


def command_of(text):
    """'/start@MyBot x' -> '/start'. Commands addressed to another bot -> 'other-bot'."""
    if not text.startswith("/"):
        return None
    name, _, target = text.split()[0].partition("@")
    if target and target.lower() != BOT["username"].lower():
        return "other-bot"
    return name.lower()[:32]


async def edit_menu(api, message, text, markup):
    """Edit a menu message, whether it is a text message or a photo with a caption."""
    chat_id, message_id = message["chat"]["id"], message["message_id"]
    try:
        if message.get("photo"):
            await api.edit_message_caption(chat_id, message_id, text, markup)
        else:
            await api.edit_message_text(chat_id, message_id, text, markup)
    except TelegramAPIError as exc:
        if "not modified" not in exc.description:
            raise


async def answer(api, callback_id, text=None, alert=False):
    try:
        await api.answer_callback(callback_id, text, alert)
    except TelegramAPIError:
        pass  # callback too old; nothing to do


# ------------------------------------------------------------------ handlers
async def on_message(api, msg):
    chat = msg.get("chat") or {}
    user = msg.get("from") or {}
    text = msg.get("text") or ""
    user_id = user.get("id")
    cmd = command_of(text)
    # Kind of update only, never the text (it could be a password).
    log.info(
        "Incoming message: chat=%s user=%s kind=%s",
        chat.get("type", "?"), user_id or "?", cmd or ("text" if text else "other"),
    )
    if chat.get("type") != "private" or not user_id:
        return
    chat_id = chat["id"]

    if cmd == "/start":
        caption = welcome_text(user.get("first_name") or "there")
        if Config.START_IMG_URL:
            try:
                await api.send_photo(chat_id, Config.START_IMG_URL, caption, start_keyboard())
                return
            except Exception as exc:
                log.warning("START_IMG_URL failed (%s); falling back to text", str(exc)[:80])
        await api.send_message(chat_id, caption, start_keyboard())

    elif cmd == "/cancel":
        if not flow.cancel(user_id):  # the flow itself reports "Login cancelled"
            await api.send_message(chat_id, "Nothing to cancel.")

    elif text and not text.startswith("/") and user_id in flow.password_waiters:
        fut = flow.password_waiters.get(user_id)
        try:
            await api.delete_message(chat_id, msg["message_id"])  # remove it from the chat right away
        except Exception:
            pass
        if fut and not fut.done():
            fut.set_result(text)  # never logged


async def on_callback(api, cb):
    data = cb.get("data") or ""
    user_id = (cb.get("from") or {}).get("id")
    message = cb.get("message") or {}
    chat = message.get("chat") or {}
    private = chat.get("type") == "private"
    log.info("Incoming callback: data=%s user=%s", data[:40], user_id or "?")

    if not private:
        await answer(api, cb["id"], "Please use this bot in a private chat.", True)
        return

    chat_id, message_id = chat["id"], message["message_id"]

    if data == "generate":
        await edit_menu(
            api, message,
            "<b>Which library do you use?</b>\n\nSelect the client library for your own Telegram account.",
            library_keyboard(),
        )
        await answer(api, cb["id"])

    elif data == "back_start":
        name = (cb.get("from") or {}).get("first_name") or "there"
        await edit_menu(api, message, welcome_text(name), start_keyboard())
        await answer(api, cb["id"])

    elif data == "close":
        try:
            await api.delete_message(chat_id, message_id)
        except TelegramAPIError:
            pass
        await answer(api, cb["id"], "Closed")

    elif data.startswith("soon:"):
        await answer(
            api, cb["id"],
            f"{data[5:]} needs its own JavaScript runtime — coming soon. Use Telethon or Pyrogram for now.",
            True,
        )

    elif data in ("library:Telethon", "library:Pyrogram"):
        wait = flow.limiter.check(user_id)
        if wait:
            await answer(api, cb["id"], f"Too many attempts. Try again in {wait}s.", True)
            return
        flow.limiter.hit(user_id)
        await answer(api, cb["id"])
        flow.start(api, chat_id, user_id, data.split(":", 1)[1])

    elif data == "cancel_login":
        if flow.cancel(user_id):
            await answer(api, cb["id"], "Cancelled")
        else:
            await answer(api, cb["id"], "No active login.")
            try:
                await api.delete_message(chat_id, message_id)
            except TelegramAPIError:
                pass
    else:
        await answer(api, cb["id"])


async def handle_update(api, update):
    try:
        if "message" in update:
            await on_message(api, update["message"])
        elif "callback_query" in update:
            await on_callback(api, update["callback_query"])
    except Exception:
        log.exception("Update handler failed")  # ERROR -> console and logger group


# ------------------------------------------------------------------- polling
async def poll_updates(api):
    offset = None
    try:  # ignore commands that were sent while the bot was offline
        stale = await api.get_updates(offset=-1, limit=1, timeout=0)
        if stale:
            offset = stale[-1]["update_id"] + 1
            log.info("Skipped stale updates queued while offline (up to id %s)", stale[-1]["update_id"])
    except Exception as exc:
        log.warning("Could not skip stale updates (%s)", str(exc)[:80])

    log.info("Polling for updates (long polling, %ss)", POLL_TIMEOUT)
    delay, last_conflict = 1, 0.0
    while True:
        try:
            updates = await api.get_updates(offset=offset, timeout=POLL_TIMEOUT)
            delay = 1
        except asyncio.CancelledError:
            raise
        except TelegramAPIError as exc:
            if exc.code == 409:
                if time.monotonic() - last_conflict > 60:
                    last_conflict = time.monotonic()
                    log.warning(
                        "409 Conflict: another process is polling this bot token or a webhook "
                        "is set. Normal for a few seconds during redeploy; if it persists, stop "
                        "the other instance. (%s)", exc.description[:100],
                    )
                await asyncio.sleep(5)
            elif exc.code == 429:
                await asyncio.sleep((exc.retry_after or 5) + 1)
            else:
                log.warning("getUpdates failed (%s); retrying in %ss", exc, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
            continue

        for update in updates:
            offset = update["update_id"] + 1
            task = asyncio.create_task(handle_update(api, update))
            _tasks.add(task)
            task.add_done_callback(_tasks.discard)


# ------------------------------------------------------------ startup checks
async def check_webhook(api):
    """A webhook blocks getUpdates. Check and (optionally) remove it."""
    try:
        info = await api.call("getWebhookInfo")
        if not info.get("url"):
            return "none ✅"
        log.warning("A webhook is set for this bot token; polling cannot receive updates while it exists.")
        if not Config.AUTO_DELETE_WEBHOOK:
            return "SET ⚠️ (AUTO_DELETE_WEBHOOK=false)"
        await api.call("deleteWebhook", {"drop_pending_updates": False})
        log.warning("Webhook removed")
        return "was set → removed ✅"
    except Exception as exc:
        log.warning("Webhook check failed (%s)", str(exc)[:80])
        return "check failed"


def log_config_summary():
    for note in Config.problems():
        log.warning("CONFIG: %s", note)
    log.info(
        "Config: LOG_GROUP_ID=%s | 2FA=%s | button_styles=%s | env names containing 'LOG': %s",
        Config.LOG_GROUP_ID or "not set",
        Config.ENABLE_2FA,
        Config.BUTTON_STYLES,
        sorted(k for k in os.environ if "LOG" in k.upper()) or "none",
    )


def _loop_exception_handler(_loop, context):
    log.error("Unhandled asyncio error: %s", context.get("message", ""), exc_info=context.get("exception"))


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
    health = await start_health_server(Config.PORT)
    api = BotAPI(Config.BOT_TOKEN, Config.API_BASE)
    poller = None
    try:
        me = await api.get_me()  # fails fast on a wrong BOT_TOKEN
        BOT["username"] = me.get("username") or ""
        gl.attach(api, loop)
        log.info("Bot started as @%s (id %s)", BOT["username"], me.get("id"))

        webhook = await check_webhook(api)
        await gl.report_start(
            me,
            {
                "🌐 Webhook": webhook,
                "🔐 2FA": "enabled" if Config.ENABLE_2FA else "disabled",
                "🎨 Button styles": "on" if Config.BUTTON_STYLES else "off",
                "📡 Health port": Config.PORT,
            },
        )
        poller = asyncio.create_task(poll_updates(api))
        log.info("Ready. Send /start to the bot: you should see 'Incoming message' below.")
        await stop.wait()
        log.info("Shutdown signal received")
    finally:
        if poller:
            poller.cancel()
        try:
            await gl.report_stop()
        except Exception:
            pass
        await flow.shutdown()
        for task in list(_tasks):
            task.cancel()
        health.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception:
        log.critical("Fatal error, bot stopped", exc_info=True)
        sys.exit(1)
        
