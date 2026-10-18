"""Reports bot start/stop and errors to a Telegram logger group (LOG_GROUP_ID).

Never sends session strings, QR links, passwords or bot tokens: every outgoing
report goes through redact() first.
"""
import asyncio
import html
import logging
import platform
import re
import time
from collections import deque
from datetime import datetime, timezone

from pyrogram.enums import ParseMode

from .config import Config

log = logging.getLogger("smart-session-bot.grouplog")  # ignored by the handler below

_REDACTIONS = (
    (re.compile(r"tg://login\?token=[\w\-=%]+"), "tg://login?token=[redacted]"),
    (re.compile(r"\b\d{6,12}:[A-Za-z0-9_\-]{30,}\b"), "[redacted-bot-token]"),
    (re.compile(r"[A-Za-z0-9_\-+/=]{120,}"), "[redacted-long-string]"),
)


def redact(text):
    for pattern, repl in _REDACTIONS:
        text = pattern.sub(repl, text)
    return text


class GroupLogger:
    MAX_PER_MINUTE = 8       # flood protection for error reports
    DEDUPE_SECONDS = 60      # identical error is sent once per this window

    def __init__(self):
        self.client = None
        self.loop = None
        self._tasks = set()
        self._stamps = deque()
        self._recent = {}
        self._warned = False

    @property
    def enabled(self):
        return bool(Config.LOG_GROUP_ID) and self.client is not None

    def attach(self, client, loop):
        self.client, self.loop = client, loop

    async def send(self, html_text):
        if not self.enabled:
            return False
        try:
            await self.client.send_message(
                Config.LOG_GROUP_ID,
                redact(html_text)[:4000],
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            return True
        except Exception as exc:
            if not self._warned:  # warn once, not on every error
                self._warned = True
                log.warning(
                    "Could not send to LOG_GROUP_ID (%s: %s). Add the bot to that chat "
                    "(admin for channels) and check the id (e.g. -100xxxxxxxxxx).",
                    type(exc).__name__,
                    str(exc)[:120],
                )
            return False

    # ------------------------------------------------------------ start / stop
    async def report_start(self, me, info):
        if not Config.LOG_GROUP_ID:
            log.info("LOG_GROUP_ID not set: logger group reporting is disabled")
            return False
        try:
            import pyrogram

            pyro = pyrogram.__version__
        except Exception:
            pyro = "?"
        try:
            import telethon

            tele = telethon.__version__
        except Exception:
            tele = "?"
        lines = [
            "✅ <b>Smart String Session Bot started</b>",
            "",
            f"🤖 Bot: @{html.escape(me.username or '?')} (<code>{me.id}</code>)",
            f"🕒 {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC",
            f"🐍 Python {platform.python_version()} · Pyrogram {html.escape(str(pyro))} · Telethon {html.escape(str(tele))}",
        ]
        lines += [f"{html.escape(k)}: {html.escape(str(v))}" for k, v in info.items()]
        ok = await self.send("\n".join(lines))
        if ok:
            log.info("Start report sent to logger group")
        return ok

    async def report_stop(self):
        if not self.enabled:
            return False
        return await self.send(
            f"🛑 <b>Smart String Session Bot stopped</b>\n🕒 {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S} UTC"
        )

    # ------------------------------------------------------------ error reports
    def _allow(self, key):
        now = time.monotonic()
        while self._stamps and now - self._stamps[0] > 60:
            self._stamps.popleft()
        if len(self._stamps) >= self.MAX_PER_MINUTE:
            return False
        last = self._recent.get(key)
        if last is not None and now - last < self.DEDUPE_SECONDS:
            return False
        if len(self._recent) > 200:
            self._recent = {k: v for k, v in self._recent.items() if now - v < self.DEDUPE_SECONDS}
        self._recent[key] = now
        self._stamps.append(now)
        return True

    def report_record(self, record, text):
        """Called from the logging handler (any thread)."""
        if not self.enabled or self.loop is None or self.loop.is_closed():
            return
        if not self._allow(f"{record.name}:{text[:200]}"):
            return
        self.loop.call_soon_threadsafe(self._spawn, record.levelname, record.name, text)

    def _spawn(self, level, name, text):
        task = self.loop.create_task(self._send_record(level, name, text))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _send_record(self, level, name, text):
        body = redact(text)[-3000:]  # keep the end: the last traceback lines matter
        icon = "🚨" if level in ("ERROR", "CRITICAL") else "⚠️"
        await self.send(
            f"{icon} <b>{html.escape(level)}</b> · <code>{html.escape(name)}</code>\n"
            f"<pre>{html.escape(body)}</pre>"
        )


gl = GroupLogger()


class GroupLogHandler(logging.Handler):
    """Forwards ERROR+ (any logger) and WARNING+ (our own loggers) to the group."""

    def __init__(self, group_logger):
        super().__init__(level=logging.WARNING)
        self.gl = group_logger
        self.setFormatter(logging.Formatter("%(message)s"))  # traceback is appended

    def emit(self, record):
        try:
            if record.name.startswith("smart-session-bot.grouplog"):
                return
            if record.levelno < logging.ERROR and not record.name.startswith("smart-session-bot"):
                return
            self.gl.report_record(record, self.format(record))
        except Exception:
            pass
          
