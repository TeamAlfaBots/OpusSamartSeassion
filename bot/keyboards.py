"""Inline keyboards as plain Bot API JSON, with Bot API 9.4 coloured buttons.

Styles: PRIMARY = blue · SUCCESS = green · DANGER = red (None = default look).
Set BUTTON_STYLES=false to send plain buttons.
"""
from .config import Config

PRIMARY = "primary"   # blue
SUCCESS = "success"   # green
DANGER = "danger"     # red
_STYLES = (PRIMARY, SUCCESS, DANGER)


def btn(text, style=None, **kwargs):
    """One inline button. kwargs: callback_data=... or url=..."""
    if style is not None and style not in _STYLES:
        raise ValueError(f"style must be one of {list(_STYLES)} or None")
    button = {"text": text, **kwargs}
    if style and Config.BUTTON_STYLES:
        button["style"] = style
    return button


def _markup(rows):
    return {"inline_keyboard": rows}


def start_keyboard():
    return _markup([
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
    return _markup([
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
    return _markup([
        [btn("Cancel", DANGER, callback_data="cancel_login")],
    ])
 
