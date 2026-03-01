"""
CORELINK Telegram Bot Module

Exports bot initialization and components for easy importing.
"""

from app.bots.telegram_bot import bot, dp, start_bot
from app.bots.handlers import setup_handlers

__all__ = [
    "bot",
    "dp",
    "start_bot",
    "setup_handlers",
]
