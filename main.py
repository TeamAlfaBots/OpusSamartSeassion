import asyncio
import logging

from aiohttp import web
from pyrogram import Client, filters

from bot import flow
from bot.config import Config
from bot.keyboards import library_keyboard, start_keyboard

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
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
    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", Config.PORT).start()
    log.info("Health server listening on port %s", Config.PORT)
    return runner


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
    runner = await start_web()
    try:
        await app.start()
        log.info("Smart String Session Generator started")
        await asyncio.Event().wait()
    finally:
        await flow.shutdown()
        try:
            await app.stop()
        except Exception:
            pass
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
