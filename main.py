import asyncio
import logging
import signal
import sys

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
async def ensure_no_webhook():
    """A webhook on this token can block MTProto updates. Check and (optionally) remove it."""
    base = f"https://api.telegram.org/bot{Config.BOT_TOKEN}"  # never log this URL
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{base}/getWebhookInfo") as resp:
                info = (await resp.json()).get("result") or {}
            pending = info.get("pending_update_count", 0)
            if not info.get("url"):
                log.info("No webhook set (pending Bot API updates: %s)", pending)
                return f"none ✅ (pending {pending})"
            log.warning(
                "A webhook is set for this bot token. It can stop updates from reaching "
                "this bot (/start gets no reply). last_error=%s",
                info.get("last_error_message"),
            )
            if not Config.AUTO_DELETE_WEBHOOK:
                return "SET ⚠️ (AUTO_DELETE_WEBHOOK=false)"
            async with session.post(f"{base}/deleteWebhook") as resp:
                ok = bool((await resp.json()).get("ok"))
            log.warning("Webhook removal %s", "succeeded" if ok else "FAILED")
            return "was set → removed ✅" if ok else "set, removal FAILED ⚠️"
    except Exception as exc:
        log.warning("Webhook check failed (%s)", type(exc).__name__)
        return "check failed"


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

    runner = await start_web()
    try:
        await app.start()
        me = await app.get_me()
        gl.attach(app, loop)
        log.info("Bot started as @%s (id %s)", me.username, me.id)

        webhook = await ensure_no_webhook()
        await gl.report_start(
            me,
            {
                "🌐 Webhook": webhook,
                "🔐 2FA": "enabled" if Config.ENABLE_2FA else "disabled",
                "🎨 Button styles": "on" if Config.BUTTON_STYLES else "off",
                "📡 Health port": Config.PORT,
            },
        )
        log.info("Smart String Session Generator is ready, waiting for updates")
        await stop.wait()
        log.info("Shutdown signal received")
    finally:
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

