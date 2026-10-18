import os

from dotenv import load_dotenv

# Load .env BEFORE reading any variable (fixes the old import-order bug).
load_dotenv()


def _int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return int(default)


def _bool(name, default="false"):
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


class Config:
    API_ID = _int("API_ID", 0)
    API_HASH = os.getenv("API_HASH", "")
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    PORT = _int("PORT", 8080)

    START_IMG_URL = os.getenv("https://files.catbox.moe/eqh9dn.png", "")
    SUPPORT_URL = os.getenv("SUPPORT_URL", "https://t.me/Alfabots_support")
    UPDATE_URL = os.getenv("UPDATE_URL", "https://t.me/Alfabots_update")
    DEVELOPER_URL = os.getenv("DEVELOPER_URL", "https://t.me/Uffsexyboi")
    OWNER_URL = os.getenv("OWNER_URL", "https://t.me/Seductive_caffine")

    # Two-step verification: the bot has to receive the user's cloud password in
    # chat, so it is OFF unless the owner explicitly enables it.
    ENABLE_2FA = _bool("ENABLE_2FA", "false")

    # Bot API 9.4 coloured buttons (blue/green/red). Needs a Pyrogram fork that
    # supports button styles; otherwise buttons silently stay plain.
    BUTTON_STYLES = _bool("BUTTON_STYLES", "true")

    QR_TOTAL_TIMEOUT = _int("QR_TOTAL_TIMEOUT", 180)    # whole login attempt (sec)
    QR_REFRESH_SECONDS = _int("QR_REFRESH_SECONDS", 25)  # QR tokens live ~30s
    PASSWORD_TIMEOUT = _int("PASSWORD_TIMEOUT", 120)
    MAX_ATTEMPTS = _int("MAX_ATTEMPTS", 5)               # per user ...
    ATTEMPT_WINDOW = _int("ATTEMPT_WINDOW", 600)         # ... per this many sec

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
            
