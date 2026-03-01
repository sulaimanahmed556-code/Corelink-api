"""
CORELINK Telegram Bot Handlers

Registers all bot handlers and routers with the dispatcher.
"""

from app.logger import logger
from app.bots.telegram_bot import dp
from app.bots.commands import router as commands_router
from app.bots.messages import router as messages_router


def setup_handlers() -> None:
    """
    Register all bot handlers with the dispatcher.
    
    Call this function before starting the bot to ensure
    all commands and message handlers are registered.
    
    Usage:
        from app.bots.handlers import setup_handlers
        
        setup_handlers()
    """
    # Register command handlers (priority)
    dp.include_router(commands_router)
    
    # Register message handlers
    dp.include_router(messages_router)
    
    logger.info("Bot handlers registered successfully")
