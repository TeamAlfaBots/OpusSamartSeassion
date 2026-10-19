import os

from dotenv import load_dotenv

# Load .env BEFORE reading any variable (fixes the old import-order bug).
load_dotenv()


# Problems found while reading env vars; main.py prints them at startup so a bad
# value is never silently ignored.
NOTES = []


def _clean(raw):
    """Strip spaces/quotes, and tolerate a pasted 'NAME=value' as the value."""
    value = raw.strip().strip("\"'").strip()
    if "=" in value:
        value = value.split("=")[-1].strip().strip("\"'").strip()
    return value.replace(" ", "")


def _first_env(names):
    for name in names:
        raw = os.getenv(name)
        if raw is not None and raw.strip():
            return name, raw
    return None, None


def _int(names, default):
    """Read an int from one env var (or the first set of several alias names)."""
    if isinstance(names, str):
        names = (names,)
    name, raw = _first_env(names)
    if raw is None:
        return int(default)
    try:
        return int(_clean(raw))
    except ValueError:
        NOTES.append(f"{name} is set but is not a number, ignoring it (using {default})")
        return int(default)


def _bool(name, default="false"):
    return os.getenv(name, default).strip().strip("\"'").lower() in {"1", "true", "yes", "on"}


def _text(name, default=""):
    return os.getenv(name, default).strip().strip("\"'").strip()


class Config:
    API_ID = _int("API_ID", 0)
    API_HASH = _text("API_HASH")
    BOT_TOKEN = _text("BOT_TOKEN")
    PORT = _int("PORT", 8080)

    START_IMG_URL = os.getenv("START_IMG_URL", "")
    SUPPORT_URL = os.getenv("SUPPORT_URL", "https://t.me/Alfabots_support")
    UPDATE_URL = os.getenv("UPDATE_URL", "https://t.me/Alfabots_update")
    DEVELOPER_URL = os.getenv("DEVELOPER_URL", "https://t.me/Uffsexyboi")
    OWNER_URL = os.getenv("OWNER_URL", "https://t.me/Seductive_caffine")

    # Two-step verification: the bot has to receive the user's cloud password in
    # chat, so it is OFF unless the owner explicitly enables it.
    ENABLE_2FA = _bool("ENABLE_2FA", "false")

    # Bot API 9.4 coloured buttons (blue/green/red), sent natively as "style".
    BUTTON_STYLES = _bool("BUTTON_STYLES", "true")

    # Bot API endpoint (change only if you run a local Bot API server).
    API_BASE = _text("TELEGRAM_API_BASE", "https://api.telegram.org")

    # Logger group/channel: the bot reports start/stop and errors there.
    # Add the bot to that chat first (admin needed for channels). e.g. -1001234567890
    LOG_GROUP_ID = _int(("LOG_GROUP_ID", "LOGGER_ID", "LOG_CHANNEL_ID", "LOGGER_GROUP_ID"), 0)

    # A webhook on this bot token blocks long polling (no updates arrive).
    # If true, the bot removes it at startup.
    AUTO_DELETE_WEBHOOK = _bool("AUTO_DELETE_WEBHOOK", "true")

    QR_TOTAL_TIMEOUT = _int("QR_TOTAL_TIMEOUT", 180)    # whole login attempt (sec)
    QR_REFRESH_SECONDS = _int("QR_REFRESH_SECONDS", 25)  # QR tokens live ~30s
    PASSWORD_TIMEOUT = _int("PASSWORD_TIMEOUT", 120)
    MAX_ATTEMPTS = _int("MAX_ATTEMPTS", 5)               # per user ...
    ATTEMPT_WINDOW = _int("ATTEMPT_WINDOW", 600)         # ... per this many sec

    @classmethod
    def problems(cls):
        """Human-readable config warnings (printed once at startup)."""
        notes = list(NOTES)
        if cls.LOG_GROUP_ID > 0:
            notes.append(
                "LOG_GROUP_ID is positive; groups/channels use a negative id "
                "like -1001234567890 (positive ids are users)"
            )
        return notes

    @classmethod
    def validate(cls):
        missing = [
            name
            for name, ok in (
                ("API_ID", bool(cls.API_ID)),
                ("API_HASH", bool(cls.API_HASH)),
                ("BOT_TOKEN", bool(cls.BOT_TOKEN)),
            )
            if not ok
        ]
        if missing:
            raise RuntimeError("Set these environment variables: " + ", ".join(missing))
            
