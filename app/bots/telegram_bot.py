"""
CORELINK Telegram Bot Initialization

Aiogram 3.x bot setup with webhook support for FastAPI integration.
"""

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Update
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from app.logger import logger

from app.config import settings


def initBot():
    bot = Bot(
        token=settings.TELEGRAM_BOT_TOKEN,
        default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
        )
    )
    return bot


# Initialize Bot with HTML parse mode
bot = initBot()



# Initialize Dispatcher
dp = Dispatcher()


async def verify_webhook_secret(request: Request) -> bool:
    """
    Verify Telegram webhook request using X-Telegram-Bot-Api-Secret-Token header.
    
    Args:
        request: FastAPI request object
        
    Returns:
        True if secret is valid, False otherwise
    """
    secret_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    return secret_token == settings.TELEGRAM_WEBHOOK_SECRET


async def telegram_webhook_handler(request: Request) -> Response:
    """
    Handle incoming Telegram webhook requests.
    
    Validates webhook secret and processes updates through aiogram dispatcher.
    
    Args:
        request: FastAPI request object
        
    Returns:
        Response with status 200 on success, 401 on auth failure
    """
    global bot
    if bot is None:
        bot = initBot()
    
    # Verify webhook secret
    if not await verify_webhook_secret(request):
        return JSONResponse(
            content={"error": "Unauthorized"},
            status_code=status.HTTP_401_UNAUTHORIZED
        )
    
    # Parse update from request body
    try:
        update_data = await request.json()
        update = Update(**update_data)
        
        # Feed update to dispatcher
        await dp.feed_update(bot=bot, update=update)
        
        return Response(status_code=status.HTTP_200_OK)
    
    except Exception as e:
        # Log error but return 200 to prevent Telegram from retrying
        logger.error(f"Error processing webhook: {e}")
        return Response(status_code=status.HTTP_200_OK)


async def on_startup() -> None:
    """
    Configure webhook on bot startup.
    
    Sets up Telegram webhook URL with secret token for validation.
    """
    global bot
    if bot is None:
        bot =  initBot()
    webhook_url = settings.TELEGRAM_WEBHOOK_URL
    
    if not webhook_url:
        raise ValueError(
            "TELEGRAM_WEBHOOK_URL not set in config. "
            "Please set it to your webhook endpoint."
        )
    
    # Set webhook with secret token
    await bot.set_webhook(
        url=webhook_url,
        secret_token=settings.TELEGRAM_WEBHOOK_SECRET,
        drop_pending_updates=True  # Ignore updates received while bot was down
    )
    
    logger.info(f"Webhook set to: {webhook_url}=========================================")


async def on_shutdown() -> None:
    """
    Clean up on bot shutdown.
    
    Removes webhook and closes bot session.
    """
    await bot.delete_webhook(drop_pending_updates=True)
    await bot.session.close()
    logger.info("Bot shutdown complete")


def start_bot(app: FastAPI) -> None:
    """
    Initialize and configure Telegram bot for FastAPI application.
    
    Sets up:
    - Webhook endpoint at /api/v1/webhook/telegram
    - Startup/shutdown event handlers
    - Bot and dispatcher lifecycle management
    
    Args:
        app: FastAPI application instance
        
    Usage:
        from fastapi import FastAPI
        from app.bots.telegram_bot import start_bot
        
        app = FastAPI()
        start_bot(app)
    """
    # Register webhook endpoint
    @app.post(f"{settings.API_V1_PREFIX}/webhook/telegram")
    async def telegram_webhook(request: Request) -> Response:
        return await telegram_webhook_handler(request)
    
    # Register startup handler
    @app.on_event("startup")
    async def startup():
        await on_startup()
    
    # Register shutdown handler
    @app.on_event("shutdown")
    async def shutdown():
        await on_shutdown()
    
    logger.info("Telegram bot initialized with webhook support")
