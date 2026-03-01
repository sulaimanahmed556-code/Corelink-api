"""
CORELINK Webhook Routes

Handles incoming webhook requests from external services with security enforcement.
"""

import time
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request, Response, Header, status, Depends
from fastapi.responses import JSONResponse
from loguru import logger
from redis.asyncio import Redis
from aiogram.types import Update

from app.bots.telegram_bot import bot, dp
from app.config import settings
from app.dependencies import get_redis


router = APIRouter()


def get_client_ip(request: Request) -> str:
    """
    Extract client IP address from request, considering reverse proxy headers.
    
    Args:
        request: FastAPI request object
        
    Returns:
        Client IP address
    """
    # Check X-Forwarded-For header (set by nginx)
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    
    # Check X-Real-IP header
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip
    
    # Fallback to direct connection IP
    if request.client:
        return request.client.host
    
    return "unknown"


async def check_rate_limit(
    redis: Redis,
    identifier: str,
    max_requests: int = 30,
    window_seconds: int = 60
) -> tuple[bool, int, int]:
    """
    Check if request is within rate limit using Redis sliding window.
    
    Args:
        redis: Redis client
        identifier: Unique identifier (e.g., IP address)
        max_requests: Maximum requests allowed in window
        window_seconds: Time window in seconds
        
    Returns:
        Tuple of (is_allowed, current_count, retry_after_seconds)
    """
    key = f"rate_limit:telegram_webhook:{identifier}"
    now = time.time()
    window_start = now - window_seconds
    
    try:
        # Use Redis sorted set for sliding window rate limiting
        pipe = redis.pipeline()
        
        # Remove old entries outside the window
        pipe.zremrangebyscore(key, 0, window_start)
        
        # Count requests in current window
        pipe.zcard(key)
        
        # Add current request
        pipe.zadd(key, {str(now): now})
        
        # Set expiry on key
        pipe.expire(key, window_seconds)
        
        results = await pipe.execute()
        current_count = results[1]
        
        if current_count >= max_requests:
            # Get oldest request in window to calculate retry_after
            oldest = await redis.zrange(key, 0, 0, withscores=True)
            if oldest:
                oldest_time = oldest[0][1]
                retry_after = int(window_seconds - (now - oldest_time)) + 1
            else:
                retry_after = window_seconds
            
            return False, current_count, retry_after
        
        return True, current_count + 1, 0
        
    except Exception as e:
        logger.error(f"Rate limit check failed: {e}")
        # On Redis failure, allow request (fail open for availability)
        return True, 0, 0


async def log_failed_attempt(
    redis: Redis,
    client_ip: str,
    reason: str,
    update_data: Optional[dict] = None
) -> None:
    """
    Log failed webhook attempt to Redis and application logs.
    
    Args:
        redis: Redis client
        client_ip: Client IP address
        reason: Reason for failure
        update_data: Telegram update data (optional)
    """
    timestamp = datetime.utcnow().isoformat()
    
    # Extract group_id if available
    group_id = None
    if update_data:
        try:
            if "message" in update_data and "chat" in update_data["message"]:
                chat = update_data["message"]["chat"]
                if chat.get("type") in ["group", "supergroup"]:
                    group_id = chat.get("id")
            elif "callback_query" in update_data:
                callback = update_data["callback_query"]
                if "message" in callback and "chat" in callback["message"]:
                    chat = callback["message"]["chat"]
                    if chat.get("type") in ["group", "supergroup"]:
                        group_id = chat.get("id")
        except Exception as e:
            logger.warning(f"Failed to extract group_id: {e}")
    
    # Log to application logs
    logger.warning(
        f"Telegram webhook failed attempt:\n"
        f"  Timestamp: {timestamp}\n"
        f"  IP: {client_ip}\n"
        f"  Reason: {reason}\n"
        f"  Group ID: {group_id or 'N/A'}\n"
        f"  Update ID: {update_data.get('update_id') if update_data else 'N/A'}"
    )
    
    # Store in Redis for monitoring (keep for 24 hours)
    try:
        log_key = f"webhook_failed:{client_ip}:{int(time.time())}"
        log_data = {
            "timestamp": timestamp,
            "ip": client_ip,
            "reason": reason,
            "group_id": str(group_id) if group_id else None,
            "update_id": str(update_data.get("update_id")) if update_data else None,
        }
        
        await redis.hset(log_key, mapping=log_data)
        await redis.expire(log_key, 86400)  # 24 hours
        
        # Increment failure counter for IP
        counter_key = f"webhook_failures:{client_ip}"
        await redis.incr(counter_key)
        await redis.expire(counter_key, 3600)  # 1 hour
        
    except Exception as e:
        logger.error(f"Failed to log webhook attempt to Redis: {e}")


@router.post("/telegram")
async def telegram_webhook(
    request: Request,
    redis: Redis = Depends(get_redis),
    x_telegram_bot_api_secret_token: str | None = Header(None, alias="X-Telegram-Bot-Api-Secret-Token")
) -> Response:
    """
    Handle incoming Telegram webhook updates with security enforcement.
    
    Security Features:
    - Webhook secret validation
    - Redis-based rate limiting (30 req/min per IP)
    - Failed attempt logging with IP and group_id
    - Detailed security logging
    
    Telegram sends updates via POST requests with:
    - X-Telegram-Bot-Api-Secret-Token header for validation
    - Update object in JSON body
    
    Args:
        request: FastAPI request object
        redis: Redis client for rate limiting
        x_telegram_bot_api_secret_token: Webhook secret header
        
    Returns:
        200 OK on success
        403 Forbidden on invalid/missing secret
        429 Too Many Requests on rate limit exceeded
        
    Note:
        Valid updates are passed to aiogram dispatcher for processing.
    """
    client_ip = get_client_ip(request)
    update_data = None
    
    try:
        # Parse update data early for logging
        update_data = await request.json()
    except Exception as e:
        logger.error(f"Failed to parse webhook body from {client_ip}: {e}")
        await log_failed_attempt(redis, client_ip, "invalid_json")
        return JSONResponse(
            content={"ok": False, "error": "Invalid JSON"},
            status_code=status.HTTP_400_BAD_REQUEST
        )
    
    # ========================================================================
    # Security Check 1: Validate Webhook Secret
    # ========================================================================
    if not x_telegram_bot_api_secret_token:
        await log_failed_attempt(redis, client_ip, "missing_secret", update_data)
        return JSONResponse(
            content={"ok": False, "error": "Missing webhook secret"},
            status_code=status.HTTP_403_FORBIDDEN
        )
    
    if x_telegram_bot_api_secret_token != settings.TELEGRAM_WEBHOOK_SECRET:
        await log_failed_attempt(redis, client_ip, "invalid_secret", update_data)
        return JSONResponse(
            content={"ok": False, "error": "Invalid webhook secret"},
            status_code=status.HTTP_403_FORBIDDEN
        )
    
    # ========================================================================
    # Security Check 2: Rate Limiting
    # ========================================================================
    is_allowed, current_count, retry_after = await check_rate_limit(
        redis,
        identifier=client_ip,
        max_requests=30,  # 30 requests
        window_seconds=60  # per minute
    )
    
    if not is_allowed:
        logger.warning(
            f"Rate limit exceeded for {client_ip}: "
            f"{current_count} requests in last 60 seconds"
        )
        await log_failed_attempt(redis, client_ip, "rate_limit_exceeded", update_data)
        
        return JSONResponse(
            content={
                "ok": False,
                "error": "Rate limit exceeded",
                "retry_after": retry_after
            },
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(retry_after)}
        )
    
    # ========================================================================
    # Process Valid Webhook
    # ========================================================================
    try:
        # Parse and validate update
        update = Update(**update_data)
        
        # Log successful webhook (debug level)
        logger.debug(
            f"Valid Telegram webhook: IP={client_ip}, "
            f"update_id={update.update_id}, "
            f"rate_limit={current_count}/30"
        )
        
        # Feed update to aiogram dispatcher
        # This is fire-and-forget to allow immediate response to Telegram
        await dp.feed_update(bot=bot, update=update)
        
        # Return success immediately
        return Response(status_code=status.HTTP_200_OK)
        
    except Exception as e:
        # Log error but still return 200 to prevent Telegram from retrying
        logger.error(
            f"Error processing webhook from {client_ip}: {e}\n"
            f"Update data: {update_data}"
        )
        await log_failed_attempt(redis, client_ip, f"processing_error: {str(e)}", update_data)
        
        # Return 200 to prevent Telegram retries
        return Response(status_code=status.HTTP_200_OK)


@router.get("/telegram/stats")
async def telegram_webhook_stats(redis: Redis = Depends(get_redis)) -> JSONResponse:
    """
    Get Telegram webhook statistics and failed attempts.
    
    Returns:
        JSON response with webhook statistics
        
    Note:
        In production, protect this endpoint with authentication.
    """
    try:
        # Get all failed attempt keys
        failed_keys = []
        cursor = 0
        while True:
            cursor, keys = await redis.scan(
                cursor,
                match="webhook_failed:*",
                count=100
            )
            failed_keys.extend(keys)
            if cursor == 0:
                break
        
        # Get failure counters
        counter_keys = []
        cursor = 0
        while True:
            cursor, keys = await redis.scan(
                cursor,
                match="webhook_failures:*",
                count=100
            )
            counter_keys.extend(keys)
            if cursor == 0:
                break
        
        # Collect failure data
        failed_attempts = []
        for key in failed_keys[:50]:  # Limit to last 50 attempts
            data = await redis.hgetall(key)
            if data:
                failed_attempts.append(data)
        
        # Collect IP failure counts
        ip_failures = {}
        for key in counter_keys:
            ip = key.replace("webhook_failures:", "")
            count = await redis.get(key)
            if count:
                ip_failures[ip] = int(count)
        
        # Sort by failure count
        top_failing_ips = sorted(
            ip_failures.items(),
            key=lambda x: x[1],
            reverse=True
        )[:10]
        
        return JSONResponse(
            content={
                "status": "ok",
                "statistics": {
                    "total_failed_attempts": len(failed_keys),
                    "unique_failing_ips": len(ip_failures),
                    "recent_failures": len(failed_attempts),
                },
                "top_failing_ips": [
                    {"ip": ip, "count": count}
                    for ip, count in top_failing_ips
                ],
                "recent_failed_attempts": failed_attempts[:10],
                "rate_limiting": {
                    "max_requests": 30,
                    "window_seconds": 60,
                    "description": "30 requests per minute per IP"
                }
            }
        )
        
    except Exception as e:
        logger.error(f"Failed to get webhook stats: {e}")
        return JSONResponse(
            content={"status": "error", "message": str(e)},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
