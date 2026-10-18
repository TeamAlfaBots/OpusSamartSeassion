"""Inline keyboards with Bot API 9.4 coloured buttons.

Styles (Bot API 9.4):  PRIMARY = blue · SUCCESS = green · DANGER = red
Leave ``style`` as None for Telegram's default look.

Pyrogram forks expose the style field differently, so ``btn()`` detects what the
installed library supports once at import time:

  * ``pyrogram.enums.ButtonStyle``     (PyroTGFork)       -> style=ButtonStyle.X
  * ``pyrogram.types.KeyboardButtonStyle`` (pyrogrammod)   -> style=KeyboardButtonStyle(bg_x=True)
  * plain ``style`` string parameter     (srigram/kittygram) -> style="x"
  * ``color`` parameter                  (DLGram)           -> color="green"/"red"/"blue"

If the library has no support at all (e.g. plain Pyrofork) buttons are sent
uncoloured, so the bot never crashes. Set BUTTON_STYLES=false to turn it off.
"""
import inspect
import logging

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .config import Config

log = logging.getLogger("smart-session-bot.keyboards")

PRIMARY = "primary"   # blue
SUCCESS = "success"   # green
DANGER = "danger"     # red

_COLOR_NAMES = {PRIMARY: "blue", SUCCESS: "green", DANGER: "red"}


def _detect_style_backend():
    """Return a function style_name -> dict of extra kwargs, or None if unsupported."""
    try:
        params = inspect.signature(InlineKeyboardButton.__init__).parameters
    except (TypeError, ValueError):
        return None

    if "style" in params:
        try:
            from pyrogram.enums import ButtonStyle

            return lambda s: {"style": getattr(ButtonStyle, s.upper())}
        except (ImportError, AttributeError):
            pass
        try:
            from pyrogram.types import KeyboardButtonStyle

            return lambda s: {"style": KeyboardButtonStyle(**{f"bg_{s}": True})}
        except ImportError:
            pass
        return lambda s: {"style": s}

    if "color" in params:
        return lambda s: {"color": _COLOR_NAMES[s]}

    return None


_BACKEND = _detect_style_backend() if getattr(Config, "BUTTON_STYLES", True) else None
log.info("Coloured buttons: %s", "enabled" if _BACKEND else "not available (plain buttons)")


def btn(text, style=None, **kwargs):
    """InlineKeyboardButton with an optional colour style (PRIMARY/SUCCESS/DANGER)."""
    if style is not None and style not in _COLOR_NAMES:
        raise ValueError(f"style must be one of {list(_COLOR_NAMES)} or None")
    if style and _BACKEND:
        try:
            return InlineKeyboardButton(text, **kwargs, **_BACKEND(style))
        except Exception as exc:  # unexpected fork API: fall back to a plain button
            log.warning("Button style failed (%s); using plain button", type(exc).__name__)
    return InlineKeyboardButton(text, **kwargs)


def start_keyboard():
    return InlineKeyboardMarkup([
        [
            btn("Support", PRIMARY, url=Config.SUPPORT_URL),
            btn("Updates", PRIMARY, url=Config.UPDATE_URL),
        ],
        [btn("Generate Session", SUCCESS, callback_data="generate")],
        [
            btn("Developer", url=Config.DEVELOPER_URL),
            btn("Owner", url=Config.OWNER_URL),
        ],
    ])


def library_keyboard():
    return InlineKeyboardMarkup([
        [
            btn("Telethon", PRIMARY, callback_data="library:Telethon"),
            btn("Pyrogram", PRIMARY, callback_data="library:Pyrogram"),
        ],
        [
            btn("Gram.JS 🚧", callback_data="soon:Gram.JS"),
            btn("mtcute 🚧", callback_data="soon:mtcute"),
        ],
        [btn("MTKruto 🚧", callback_data="soon:MTKruto")],
        [
            btn("Back", callback_data="back_start"),
            btn("Close", DANGER, callback_data="close"),
        ],
    ])


def qr_keyboard():
    return InlineKeyboardMarkup([
        [btn("Cancel", DANGER, callback_data="cancel_login")],
    ])
    
