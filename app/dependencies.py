"""
CORELINK FastAPI Dependencies

Dependency injection helpers for database, cache, and other services.
"""

from typing import AsyncGenerator
from redis.asyncio import Redis, from_url
from loguru import logger

from app.config import settings


# Global Redis connection pool
_redis_client: Redis | None = None


async def get_redis_client() -> Redis:
    """
    Get or create Redis client with connection pooling.
    
    Creates a single Redis client instance that is reused across requests.
    Connection pooling is handled automatically by redis-py.
    
    Returns:
        Redis client instance
        
    Raises:
        Exception: If Redis connection fails
    """
    global _redis_client
    
    if _redis_client is None:
        try:
            # Parse Redis URL and create client
            _redis_client = from_url(
                str(settings.REDIS_URL),
                encoding="utf-8",
                decode_responses=True,
                max_connections=settings.REDIS_MAX_CONNECTIONS,
                socket_connect_timeout=5,
                socket_keepalive=True,
            )
            
            # Test connection
            await _redis_client.ping()
            logger.info("Redis client initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {str(e)}")
            raise
    
    return _redis_client


async def get_redis() -> AsyncGenerator[Redis, None]:
    """
    FastAPI dependency for Redis connections.
    
    Provides a Redis client for the duration of the request.
    Connection is reused from the global pool.
    
    Yields:
        Redis client instance
        
    Usage:
        from fastapi import Depends
        from app.dependencies import get_redis
        from redis.asyncio import Redis
        
        @router.get("/cache-test")
        async def test_cache(redis: Redis = Depends(get_redis)):
            await redis.set("key", "value", ex=60)
            value = await redis.get("key")
            return {"value": value}
    """
    redis_client = await get_redis_client()
    try:
        yield redis_client
    except Exception as e:
        logger.error(f"Redis operation failed: {str(e)}")
        raise
    finally:
        # Connection is returned to pool automatically
        pass


async def init_redis() -> None:
    """
    Initialize Redis connection on application startup.
    
    Call this function in FastAPI startup event to ensure
    Redis is available before handling requests.
    
    Usage:
        @app.on_event("startup")
        async def startup():
            await init_redis()
    """
    try:
        await get_redis_client()
        logger.info("Redis connection initialized")
    except Exception as e:
        logger.error(f"Failed to initialize Redis: {str(e)}")
        raise


async def close_redis() -> None:
    """
    Close Redis connections on application shutdown.
    
    Closes the connection pool and releases resources.
    Call this function in FastAPI shutdown event.
    
    Usage:
        @app.on_event("shutdown")
        async def shutdown():
            await close_redis()
    """
    global _redis_client
    
    if _redis_client is not None:
        try:
            await _redis_client.close()
            await _redis_client.connection_pool.disconnect()
            logger.info("Redis connections closed")
        except Exception as e:
            logger.error(f"Error closing Redis: {str(e)}")
        finally:
            _redis_client = None


# Cache helper functions
async def cache_get(key: str) -> str | None:
    """
    Get value from Redis cache.
    
    Args:
        key: Cache key
        
    Returns:
        Cached value or None if not found
        
    Usage:
        value = await cache_get("user:123")
    """
    redis = await get_redis_client()
    try:
        return await redis.get(key)
    except Exception as e:
        logger.warning(f"Cache get failed for key '{key}': {str(e)}")
        return None


async def cache_set(key: str, value: str, ttl: int = 3600) -> bool:
    """
    Set value in Redis cache with TTL.
    
    Args:
        key: Cache key
        value: Value to cache
        ttl: Time to live in seconds (default: 1 hour)
        
    Returns:
        True if successful, False otherwise
        
    Usage:
        await cache_set("user:123", "data", ttl=300)
    """
    redis = await get_redis_client()
    try:
        await redis.set(key, value, ex=ttl)
        return True
    except Exception as e:
        logger.warning(f"Cache set failed for key '{key}': {str(e)}")
        return False


async def cache_delete(key: str) -> bool:
    """
    Delete key from Redis cache.
    
    Args:
        key: Cache key to delete
        
    Returns:
        True if key was deleted, False otherwise
        
    Usage:
        await cache_delete("user:123")
    """
    redis = await get_redis_client()
    try:
        result = await redis.delete(key)
        return result > 0
    except Exception as e:
        logger.warning(f"Cache delete failed for key '{key}': {str(e)}")
        return False


async def cache_exists(key: str) -> bool:
    """
    Check if key exists in Redis cache.
    
    Args:
        key: Cache key to check
        
    Returns:
        True if key exists, False otherwise
        
    Usage:
        if await cache_exists("user:123"):
            print("Key exists")
    """
    redis = await get_redis_client()
    try:
        result = await redis.exists(key)
        return result > 0
    except Exception as e:
        logger.warning(f"Cache exists check failed for key '{key}': {str(e)}")
        return False
