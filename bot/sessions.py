"""Session generation.

Login is done once, with Telethon's QR flow (auto-refreshing, 2FA-aware). A
Pyrogram string is built from the very same MTProto auth key, so both outputs
come from one well-tested login path.
"""
import asyncio
import base64
import logging
import struct

from .config import Config

log = logging.getLogger("smart-session-bot.sessions")

SUPPORTED = ("Telethon", "Pyrogram")
MAX_PASSWORD_TRIES = 3

# Pyrogram >= 2.0 / Pyrofork string layout:
# dc_id (1) | api_id (4) | test_mode (1) | auth_key (256) | user_id (8) | is_bot (1)
_PYROGRAM_FORMAT = ">BI?256sQ?"


class LoginError(Exception):
    """Base class; subclasses are handled with a safe, user-facing message."""


class LoginTimeout(LoginError):
    pass


class PasswordDisabled(LoginError):
    pass


class WrongPassword(LoginError):
    pass


class UnsupportedLibrary(LoginError):
    pass


def pyrogram_session_string(dc_id, api_id, auth_key, user_id, is_bot=False, test_mode=False):
    if len(auth_key) != 256:
        raise ValueError("auth_key must be 256 bytes")
    packed = struct.pack(_PYROGRAM_FORMAT, dc_id, api_id, test_mode, auth_key, user_id, is_bot)
    return base64.urlsafe_b64encode(packed).decode().rstrip("=")


async def _two_factor(client, ask_password):
    from telethon.errors import PasswordHashInvalidError

    if not Config.ENABLE_2FA:
        raise PasswordDisabled()
    for attempt in range(1, MAX_PASSWORD_TRIES + 1):
        password = await ask_password(attempt)
        try:
            await client.sign_in(password=password)
            return
        except PasswordHashInvalidError:
            continue
        finally:
            password = None
    raise WrongPassword()


async def _qr_login(client, on_qr, ask_password):
    """Show a QR, refresh it before it expires, handle 2FA, stop at the deadline."""
    from telethon.errors import SessionPasswordNeededError

    loop = asyncio.get_running_loop()
    deadline = loop.time() + Config.QR_TOTAL_TIMEOUT

    qr = await client.qr_login()
    await on_qr(qr.url)

    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise LoginTimeout()
        per_try = min(remaining, Config.QR_REFRESH_SECONDS)
        try:
            # Telethon's wait() only raises on timeout when a timeout is given,
            # so every round is bounded (the outer wait_for is a safety net).
            await asyncio.wait_for(qr.wait(timeout=per_try), timeout=per_try + 5)
            return
        except asyncio.TimeoutError:
            pass
        except SessionPasswordNeededError:
            await _two_factor(client, ask_password)
            return

        if deadline - loop.time() <= 1:
            raise LoginTimeout()
        try:
            await qr.recreate()
        except SessionPasswordNeededError:
            await _two_factor(client, ask_password)
            return
        await on_qr(qr.url)


async def generate_session(library, on_qr, ask_password):
    normalized = library.lower()
    if normalized not in ("telethon", "pyrogram"):
        raise UnsupportedLibrary(library)

    from telethon import TelegramClient
    from telethon.sessions import StringSession

    client = TelegramClient(
        StringSession(),
        Config.API_ID,
        Config.API_HASH,
        device_model="Smart Session Generator",
        connection_retries=3,
        request_retries=3,
    )
    try:
        await client.connect()
        await _qr_login(client, on_qr, ask_password)

        if normalized == "telethon":
            return client.session.save()

        me = await client.get_me()
        return pyrogram_session_string(
            dc_id=client.session.dc_id,
            api_id=Config.API_ID,
            auth_key=client.session.auth_key.key,
            user_id=me.id,
            is_bot=bool(getattr(me, "bot", False)),
        )
    finally:
        try:
            # disconnect only; NEVER log_out(), that would kill the new session
            await client.disconnect()
        except Exception:
            pass
