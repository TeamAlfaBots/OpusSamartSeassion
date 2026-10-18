"""Per-user login flows: one active flow per user, rate limit, password capture."""
import asyncio
import io
import logging

import qrcode
from pyrogram.types import InputMediaPhoto

from .config import Config
from .keyboards import qr_keyboard
from .limits import RateLimiter
from .sessions import (
    LoginError,
    LoginTimeout,
    PasswordDisabled,
    WrongPassword,
    generate_session,
)

log = logging.getLogger("smart-session-bot.flow")

limiter = RateLimiter(Config.MAX_ATTEMPTS, Config.ATTEMPT_WINDOW)
tasks = {}              # user_id -> asyncio.Task (one active login per user)
password_waiters = {}   # user_id -> Future waiting for the 2FA password message


def qr_png(url):
    # The URL is a live login credential: never log or persist it.
    qr = qrcode.QRCode(box_size=10, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    buf = io.BytesIO()
    buf.name = "login-qr.png"
    qr.make_image().save(buf, format="PNG")
    buf.seek(0)
    return buf


def qr_caption(library):
    return (
        f"**{library} — Scan QR to log in**\n\n"
        "Open Telegram on the device with your account and go to "
        "Settings → Devices → Link Desktop Device, then scan this QR.\n\n"
        "The QR refreshes automatically. Scan only if this is your own account."
    )


async def _safe_delete(msg):
    if msg is None:
        return
    try:
        await msg.delete()
    except Exception:
        pass


def cancel(user_id):
    task = tasks.get(user_id)
    if task and not task.done():
        task.cancel()
        return True
    return False


def start(client, chat_id, user_id, library):
    """Start a login for this user, cancelling any previous one."""
    cancel(user_id)
    task = asyncio.create_task(_run(client, chat_id, user_id, library))
    tasks[user_id] = task
    return task


async def shutdown():
    pending = [t for t in tasks.values() if not t.done()]
    for t in pending:
        t.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def _run(client, chat_id, user_id, library):
    status = None
    qr_msg = None

    async def on_qr(url):
        nonlocal qr_msg
        photo = qr_png(url)
        caption = qr_caption(library)
        if qr_msg is not None:
            try:
                qr_msg = await qr_msg.edit_media(
                    InputMediaPhoto(photo, caption=caption), reply_markup=qr_keyboard()
                )
                return
            except Exception:
                await _safe_delete(qr_msg)
                qr_msg = None
                photo = qr_png(url)
        qr_msg = await client.send_photo(
            chat_id, photo, caption=caption, reply_markup=qr_keyboard()
        )

    async def ask_password(attempt):
        nonlocal qr_msg
        if attempt == 1:
            await _safe_delete(qr_msg)  # QR already scanned, remove it
            qr_msg = None
            text = (
                "🔑 **Two-step verification is enabled on this account.**\n\n"
                "Send your cloud password as a message now. It is used once to finish "
                "the login, is never stored or logged, and your message is deleted "
                "immediately.\n\nSend /cancel to stop."
            )
        else:
            text = f"❌ Wrong password. Try again ({attempt}/3), or send /cancel."
        await client.send_message(chat_id, text)

        fut = asyncio.get_running_loop().create_future()
        password_waiters[user_id] = fut
        try:
            return await asyncio.wait_for(fut, Config.PASSWORD_TIMEOUT)
        except asyncio.TimeoutError:
            raise LoginTimeout()
        finally:
            if password_waiters.get(user_id) is fut:
                password_waiters.pop(user_id, None)

    try:
        status = await client.send_message(
            chat_id,
            f"Preparing **{library}** QR login…\n\n"
            "Only scan this QR using your own Telegram account. Do not forward the QR code.",
        )
        session = await generate_session(library, on_qr, ask_password)

        await _safe_delete(qr_msg)
        await _safe_delete(status)
        # Never log the session string. Deliver only to this private chat.
        await client.send_message(
            chat_id,
            f"✅ **{library} session generated.**\n\n"
            "Anyone who gets this string can access your Telegram account. "
            "Save it somewhere private (e.g. Saved Messages), never share it, "
            "and delete this message afterwards. If it ever leaks, terminate it in "
            "Settings → Devices.\n\n"
            f"`{session}`",
            disable_web_page_preview=True,
        )
        session = None

    except asyncio.CancelledError:
        await _safe_delete(qr_msg)
        if status is not None:
            try:
                await status.edit_text("🚫 Login cancelled.")
            except Exception:
                pass
        raise

    except LoginError as exc:
        await _safe_delete(qr_msg)
        messages = {
            LoginTimeout: "⏰ Login timed out. Tap **Generate Session** to try again.",
            PasswordDisabled: (
                "🔐 This account uses two-step verification, which this bot instance "
                "does not accept. Temporarily disable it in Telegram, or ask the "
                "owner to enable 2FA support."
            ),
            WrongPassword: "❌ Too many wrong passwords. Please start again.",
        }
        text = messages.get(type(exc), "❌ Could not complete login. Please try again.")
        log.info("Login ended: %s", type(exc).__name__)
        await _edit_or_send(client, chat_id, status, text)

    except Exception as exc:
        await _safe_delete(qr_msg)
        log.warning("Session generation failed for %s (%s)", library, type(exc).__name__)
        await _edit_or_send(
            client,
            chat_id,
            status,
            "❌ Session generation did not complete. Please try again in a moment.",
        )

    finally:
        if tasks.get(user_id) is asyncio.current_task():
            tasks.pop(user_id, None)


async def _edit_or_send(client, chat_id, status, text):
    try:
        if status is not None:
            await status.edit_text(text)
        else:
            await client.send_message(chat_id, text)
    except Exception:
        pass
