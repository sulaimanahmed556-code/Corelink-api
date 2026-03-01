"""
CORELINK - FastAPI Application

Main application entry point for the CORELINK Telegram group analytics platform.
Includes secure webhook handling with validation and logging.
"""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from loguru import logger as loguru_logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db, init_db, close_db
from app.dependencies import init_redis, close_redis
from app.api.router import api_router
from app.api.routes.payments import CreateSubscriptionRequest, create_subscription
from app.bots import setup_handlers, start_bot
from app.bots.telegram_bot import on_startup as bot_startup, on_shutdown as bot_shutdown
from app.tasks import start_scheduler, stop_scheduler
from app.middleware import WebhookSecurityMiddleware
from app.logger import logger
from app.models import Plan


# Configure loguru for enhanced logging
loguru_logger.add(
    "logs/corelink_{time:YYYY-MM-DD}.log",
    rotation="00:00",
    retention="30 days",
    level="INFO" if settings.is_production else "DEBUG",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
    compression="zip",
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """
    Manage application lifespan events.
    
    Startup:
    - Initialize database tables
    - Setup bot handlers
    - Configure Telegram webhook
    
    Shutdown:
    - Close database connections
    - Remove Telegram webhook
    """
    # Startup
    logger.info("=" * 80)
    logger.info("Starting CORELINK application...")
    logger.info("=" * 80)
    logger.info(f"Environment: {settings.ENV}")
    logger.info(f"Debug mode: {settings.DEBUG}")
    logger.info(f"HTTPS enforcement: {'enabled' if settings.is_production else 'disabled (dev mode)'}")
    logger.info(f"Webhook security: enabled")
    logger.info(f"API prefix: {settings.API_V1_PREFIX}")
    
    try:
        # Initialize database
        logger.info("Initializing database...")
        await init_db()
        logger.info("✓ Database initialized successfully")
        
        # Initialize Redis
        logger.info("Initializing Redis...")
        await init_redis()
        logger.info("✓ Redis initialized successfully")
        
        # Setup bot handlers
        logger.info("Setting up Telegram bot handlers...")
        setup_handlers()
        logger.info("✓ Bot handlers configured")
        
        # Initialize Telegram bot webhook
        logger.info("Initializing Telegram bot webhook...")
        await bot_startup()
        logger.info("✓ Telegram bot webhook configured")
        
        # Start task scheduler
        logger.info("Starting background task scheduler...")
        await start_scheduler()
        logger.info("✓ Task scheduler started")
        
        logger.info("=" * 80)
        logger.info("✓ CORELINK startup complete")
        logger.info("=" * 80)
        
        # Log security configuration
        loguru_logger.info(
            f"Security Configuration:\n"
            f"  - Webhook validation: ENABLED\n"
            f"  - HTTPS enforcement: {'ENABLED' if settings.is_production else 'DISABLED (dev)'}\n"
            f"  - Rate limiting: ENABLED (nginx)\n"
            f"  - Failed attempt tracking: ENABLED\n"
            f"  - Max failed attempts: 10\n"
            f"  - Block duration: 15 minutes"
        )
        
    except Exception as e:
        logger.error(f"Error during startup: {e}")
        raise
    
    yield
    
    # Shutdown
    logger.info("Shutting down CORELINK...")
    
    try:
        # Shutdown Telegram bot
        await bot_shutdown()
        logger.info("Telegram bot shutdown complete")
        
        # Stop task scheduler
        await stop_scheduler()
        logger.info("Task scheduler stopped")
        
        # Close Redis connections
        await close_redis()
        logger.info("Redis connections closed")
        
        # Close database connections
        await close_db()
        logger.info("Database connections closed")
        
    except Exception as e:
        logger.error(f"Error during shutdown: {e}")
    
    logger.info("CORELINK shutdown complete")


BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


app = FastAPI(
    title=settings.APP_NAME,
    description="Intelligent Telegram Group Analytics Platform with Secure Webhook Handling",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,  # Disable docs in production
    redoc_url="/redoc" if settings.DEBUG else None,
    # Trust proxy headers (nginx sets X-Forwarded-For, X-Forwarded-Proto)
    openapi_url="/openapi.json" if settings.DEBUG else None,
)


# ============================================================================
# Middleware Configuration (Order Matters!)
# ============================================================================

# 1. Webhook Security Middleware (First - validates webhook requests)
app.add_middleware(WebhookSecurityMiddleware)

# 2. Trusted Host Middleware (Prevents host header attacks)
if settings.is_production:
    # In production, only allow requests to specific hosts
    # Update this list with your actual domains
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["*"]  # TODO: Replace with actual domains in production
    )

# 3. CORS Middleware (Last - handles cross-origin requests)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


# Health check endpoint
@app.get("/health", tags=["Health"])
async def health_check() -> JSONResponse:
    """
    Health check endpoint for monitoring and load balancers.
    
    Returns:
        JSON response with service status
    """
    return JSONResponse(
        content={
            "status": "healthy",
            "service": settings.APP_NAME,
            "environment": settings.ENV,
            "version": "1.0.0",
            "https_enabled": settings.is_production,
            "webhook_security": "enabled"
        }
    )


@app.get("/payments", response_class=HTMLResponse, tags=["Payments"])
async def payments_checkout(
    request: Request,
    group_id: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    checkout_path = settings.PAYMENT_CLIENT_CHECKOUT_PATH.strip() or "/payments"
    if not checkout_path.startswith("/"):
        checkout_path = f"/{checkout_path}"

    group_id_value = group_id.strip()
    if not group_id_value:
        return templates.TemplateResponse(
            "payments/error.html",
            {
                "request": request,
                "message": "Missing required query parameter: group_id",
                "groupId": "",
                "checkoutPath": checkout_path,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    query = (
        select(Plan)
        .where(Plan.is_active.is_(True))
        .order_by(Plan.price.asc(), Plan.created_at.desc())
    )

    try:
        result = await db.execute(query)
        plans = result.scalars().all()
    except Exception as exc:
        logger.error(f"Error loading plans for checkout: {exc}")
        return templates.TemplateResponse(
            "payments/error.html",
            {
                "request": request,
                "message": "Failed to load plans",
                "groupId": group_id_value,
                "checkoutPath": checkout_path,
            },
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    from decimal import Decimal
    import json

    plans_data: list[dict[str, Any]] = []
    for plan in plans:
        price_value: Any = plan.price
        if isinstance(price_value, Decimal):
            price_value = float(price_value)

        plans_data.append(
            {
                "id": str(plan.id),
                "name": plan.name,
                "description": plan.description,
                "price": price_value,
                "currency": plan.currency,
                "stripe_plan_id": plan.stripe_plan_id,
                "paypal_plan_id": plan.paypal_plan_id,
                "paystack_plan_code": plan.paystack_plan_code,
            }
        )

    plans_json = json.dumps(plans_data).replace("<", "\\u003c")

    return templates.TemplateResponse(
        "payments/index.html",
        {
            "request": request,
            "plans": plans_data,
            "plans_json": plans_json,
            "groupId": group_id_value,
            "checkoutPath": checkout_path,
        },
    )


@app.post(
    "/payments/create-subscription",
    response_class=HTMLResponse,
    tags=["Payments"],
)
async def payments_create_subscription(
    request: Request,
    group_id: str = Form(...),
    plan_db_id: str = Form(""),
    provider: str = Form(...),
    email: str = Form(...),
    customer_name: str = Form(""),
    first_name: str = Form(""),
    last_name: str = Form(""),
    phone: str = Form(""),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    checkout_path = settings.PAYMENT_CLIENT_CHECKOUT_PATH.strip() or "/payments"
    if not checkout_path.startswith("/"):
        checkout_path = f"/{checkout_path}"

    group_id_value = group_id.strip()

    try:
        payload = CreateSubscriptionRequest(
            group_id=group_id_value,
            provider=provider,
            email=email,
            plan_db_id=plan_db_id or None,
            customer_name=customer_name or None,
            customer_info={
                "first_name": first_name,
                "last_name": last_name,
                "phone": phone,
            },
        )
    except Exception as exc:
        logger.error(f"Invalid subscription form data: {exc}")
        return templates.TemplateResponse(
            "payments/error.html",
            {
                "request": request,
                "message": "Invalid subscription data",
                "groupId": group_id_value,
                "checkoutPath": checkout_path,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        result = await create_subscription(payload, db)
    except HTTPException as exc:
        return templates.TemplateResponse(
            "payments/error.html",
            {
                "request": request,
                "message": str(exc.detail),
                "groupId": group_id_value,
                "checkoutPath": checkout_path,
            },
            status_code=exc.status_code,
        )
    except Exception as exc:
        logger.error(f"Subscription creation failed: {exc}")
        return templates.TemplateResponse(
            "payments/error.html",
            {
                "request": request,
                "message": "Subscription creation failed",
                "groupId": group_id_value,
                "checkoutPath": checkout_path,
            },
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if result.payment_url:
        return RedirectResponse(
            url=result.payment_url,
            status_code=status.HTTP_302_FOUND,
        )

    return templates.TemplateResponse(
        "payments/result.html",
        {
            "request": request,
            "result": result,
            "groupId": group_id_value,
            "checkoutPath": checkout_path,
        },
    )


# Security monitoring endpoint (admin only in production)
@app.get("/security/webhook-stats", tags=["Security"])
async def webhook_security_stats() -> JSONResponse:
    """
    Get webhook security statistics.
    
    Returns:
        JSON response with security metrics
        
    Note:
        In production, this should be protected with authentication.
        Consider adding admin authentication before deploying.
    """
    from app.middleware import get_webhook_stats
    
    stats = get_webhook_stats()
    
    return JSONResponse(
        content={
            "status": "ok",
            "webhook_security": stats,
            "environment": settings.ENV,
            "https_enforced": settings.is_production,
        }
    )


# Include API router
app.include_router(
    api_router,
    prefix=settings.API_V1_PREFIX
)


# Initialize Telegram bot with webhook
start_bot(app)


# Root endpoint
@app.get("/", tags=["Root"])
async def root() -> JSONResponse:
    """
    Root endpoint with API information.
    
    Returns:
        JSON response with API details
    """
    return JSONResponse(
        content={
            "name": settings.APP_NAME,
            "description": "Intelligent Telegram Group Analytics Platform",
            "version": "1.0.0",
            "environment": settings.ENV,
            "docs": "/docs" if settings.DEBUG else "disabled",
            "health": "/health"
        }
    )


if __name__ == "__main__":
    import uvicorn
    
    # Development server
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        log_level="debug" if settings.DEBUG else "info"
    )
