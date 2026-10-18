from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .config import Config


def start_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Support", url=Config.SUPPORT_URL),
            InlineKeyboardButton("Updates", url=Config.UPDATE_URL),
        ],
        [InlineKeyboardButton("Generate Session", callback_data="generate")],
        [
            InlineKeyboardButton("Developer", url=Config.DEVELOPER_URL),
            InlineKeyboardButton("Owner", url=Config.OWNER_URL),
        ],
    ])


def library_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Telethon", callback_data="library:Telethon"),
            InlineKeyboardButton("Pyrogram", callback_data="library:Pyrogram"),
        ],
        [
            InlineKeyboardButton("Gram.JS 🚧", callback_data="soon:Gram.JS"),
            InlineKeyboardButton("mtcute 🚧", callback_data="soon:mtcute"),
        ],
        [InlineKeyboardButton("MTKruto 🚧", callback_data="soon:MTKruto")],
        [
            InlineKeyboardButton("Back", callback_data="back_start"),
            InlineKeyboardButton("Close", callback_data="close"),
        ],
    ])


def qr_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Cancel", callback_data="cancel_login")],
    ])
