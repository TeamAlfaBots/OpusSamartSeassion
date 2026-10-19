"""Minimal Telegram Bot API client (standard library only).

Blocking urllib calls run in a thread pool so the asyncio loop never blocks.
The bot token only lives inside the request URL; it is never put in exception
messages or logs.
"""
import asyncio
import json
import logging
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("smart-session-bot.api")

DEFAULT_BASE = "https://api.telegram.org"


class TelegramAPIError(Exception):
    def __init__(self, code, description, retry_after=None):
        super().__init__(f"{code}: {description}")
        self.code = code
        self.description = description
        self.retry_after = retry_after


def _field(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def _multipart(fields, files):
    boundary = uuid.uuid4().hex
    chunks = []
    for name, value in fields.items():
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
            + _field(value).encode()
            + b"\r\n"
        )
    for name, (filename, data, ctype) in files.items():
        chunks.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                f'filename="{filename}"\r\nContent-Type: {ctype}\r\n\r\n'
            ).encode()
            + data
            + b"\r\n"
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class Msg:
    """Handle to a message the bot sent."""

    def __init__(self, api, chat_id, message_id):
        self.api, self.chat_id, self.message_id = api, chat_id, message_id

    async def delete(self):
        await self.api.delete_message(self.chat_id, self.message_id)

    async def edit_text(self, text, reply_markup=None):
        await self.api.edit_message_text(self.chat_id, self.message_id, text, reply_markup)

    async def edit_photo(self, photo, caption, reply_markup=None):
        await self.api.edit_message_photo(self.chat_id, self.message_id, photo, caption, reply_markup)


class BotAPI:
    def __init__(self, token, base=DEFAULT_BASE, max_threads=16):
        self._base = base.rstrip("/")
        self._token = token
        self._pool = ThreadPoolExecutor(max_workers=max_threads, thread_name_prefix="botapi")

    # ------------------------------------------------------------- transport
    def _call_sync(self, method, params, files, timeout):
        url = f"{self._base}/bot{self._token}/{method}"  # never log this URL
        if files:
            body, ctype = _multipart(params or {}, files)
        else:
            body, ctype = json.dumps(params or {}).encode(), "application/json"
        request = urllib.request.Request(url, data=body, headers={"Content-Type": ctype}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8"))
            except Exception:
                raise TelegramAPIError(exc.code, "HTTP error") from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise TelegramAPIError(0, f"network error ({type(exc).__name__})") from None
        if not payload.get("ok"):
            raise TelegramAPIError(
                payload.get("error_code", 0),
                str(payload.get("description")),
                (payload.get("parameters") or {}).get("retry_after"),
            )
        return payload.get("result")

    async def call(self, method, params=None, files=None, timeout=30):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, self._call_sync, method, params, files, timeout)

    # --------------------------------------------------------------- methods
    async def get_me(self):
        return await self.call("getMe")

    async def get_updates(self, offset=None, timeout=25, limit=100):
        params = {"timeout": timeout, "limit": limit, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        return await self.call("getUpdates", params, timeout=timeout + 10)

    async def send_message(self, chat_id, text, reply_markup=None):
        params = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        if reply_markup:
            params["reply_markup"] = reply_markup
        res = await self.call("sendMessage", params)
        return Msg(self, res["chat"]["id"], res["message_id"])

    async def send_photo(self, chat_id, photo, caption, reply_markup=None):
        """photo: bytes (uploaded) or a str URL/file_id."""
        params = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}
        if reply_markup:
            params["reply_markup"] = reply_markup
        files = None
        if isinstance(photo, (bytes, bytearray)):
            files = {"photo": ("qr.png", bytes(photo), "image/png")}
        else:
            params["photo"] = photo
        res = await self.call("sendPhoto", params, files, timeout=60)
        return Msg(self, res["chat"]["id"], res["message_id"])

    async def edit_message_text(self, chat_id, message_id, text, reply_markup=None):
        params = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        if reply_markup:
            params["reply_markup"] = reply_markup
        return await self.call("editMessageText", params)

    async def edit_message_caption(self, chat_id, message_id, caption, reply_markup=None):
        params = {"chat_id": chat_id, "message_id": message_id, "caption": caption, "parse_mode": "HTML"}
        if reply_markup:
            params["reply_markup"] = reply_markup
        return await self.call("editMessageCaption", params)

    async def edit_message_photo(self, chat_id, message_id, photo, caption, reply_markup=None):
        params = {
            "chat_id": chat_id,
            "message_id": message_id,
            "media": {"type": "photo", "media": "attach://photo", "caption": caption, "parse_mode": "HTML"},
        }
        if reply_markup:
            params["reply_markup"] = reply_markup
        files = {"photo": ("qr.png", bytes(photo), "image/png")}
        return await self.call("editMessageMedia", params, files, timeout=60)

    async def delete_message(self, chat_id, message_id):
        return await self.call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})

    async def answer_callback(self, callback_id, text=None, show_alert=False):
        params = {"callback_query_id": callback_id}
        if text:
            params["text"] = text
            params["show_alert"] = show_alert
        return await self.call("answerCallbackQuery", params)
      
