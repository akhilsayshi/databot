from __future__ import annotations

import json
import time
from typing import Any, Optional, Union
from functools import wraps

import redis
from redis.exceptions import RedisError, ConnectionError, TimeoutError

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

_redis_client: Optional[redis.Redis] = None
_cache_enabled: bool = True


def get_redis() -> redis.Redis:
    """Get Redis client instance with lazy initialization and connection pooling."""
    global _redis_client
    if _redis_client is None:
        try:
            # Configure Redis client with connection pooling
            _redis_client = redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
                health_check_interval=30,
                max_connections=20
            )
            
            # Test connection
            _redis_client.ping()
            logger.info("Redis connection established", extra={
                "redis_url": settings.redis_url.split("@")[-1] if "@" in settings.redis_url else "local",
                "max_connections": 20
            })
            
        except RedisError as e:
            logger.error("Failed to connect to Redis", extra={
                "redis_url": settings.redis_url,
                "error": str(e),
                "error_type": type(e).__name__
            })
            _cache_enabled = False
            raise
    
    return _redis_client


def check_redis_health() -> bool:
    """Check if Redis is healthy and accessible."""
    global _cache_enabled
    
    if not _cache_enabled:
        return False
    
    try:
        redis_client = get_redis()
        redis_client.ping()
        return True
    except Exception as e:
        logger.warning("Redis health check failed", extra={
            "error": str(e),
            "error_type": type(e).__name__
        })
        _cache_enabled = False
        return False


def wait_for_redis(max_retries: int = 30, retry_delay: float = 2.0) -> bool:
    """Wait for Redis to become available."""
    logger.info("Waiting for Redis to become available...")
    
    for attempt in range(max_retries):
        if check_redis_health():
            logger.info("Redis is available", extra={"attempts": attempt + 1})
            return True
        
        logger.warning(f"Redis not available, retrying in {retry_delay}s...", extra={
            "attempt": attempt + 1,
            "max_retries": max_retries
        })
        time.sleep(retry_delay)
    
    logger.error("Redis failed to become available", extra={"max_retries": max_retries})
    return False


def cache_get_json(key: str) -> Optional[dict[str, Any]]:
    """Get JSON data from cache with fallback."""
    if not _cache_enabled:
        logger.debug("Cache disabled, skipping get", extra={"cache_key": key})
        return None
    
    try:
        redis_client = get_redis()
        raw = redis_client.get(key)
        
        if not raw:
            logger.debug("Cache miss", extra={"cache_key": key})
            return None
        
        data = json.loads(raw)
        logger.debug("Cache hit", extra={"cache_key": key})
        return data
        
    except json.JSONDecodeError as e:
        logger.warning("Invalid JSON in cache", extra={
            "cache_key": key,
            "error": str(e)
        })
        # Remove invalid data from cache
        try:
            get_redis().delete(key)
        except:
            pass
        return None
        
    except (RedisError, ConnectionError, TimeoutError) as e:
        logger.error("Redis error during cache get", extra={
            "cache_key": key,
            "error": str(e),
            "error_type": type(e).__name__
        })
        return None
        
    except Exception as e:
        logger.error("Unexpected error during cache get", extra={
            "cache_key": key,
            "error": str(e),
            "error_type": type(e).__name__
        })
        return None


def cache_set_json(key: str, value: dict[str, Any], ttl_seconds: int) -> bool:
    """Set JSON data in cache with TTL. Returns True if successful."""
    if not _cache_enabled:
        logger.debug("Cache disabled, skipping set", extra={"cache_key": key})
        return False
    
    try:
        json_data = json.dumps(value, default=str)  # Handle datetime objects
        redis_client = get_redis()
        redis_client.setex(key, ttl_seconds, json_data)
        
        logger.debug("Cache set successfully", extra={
            "cache_key": key,
            "ttl_seconds": ttl_seconds
        })
        return True
        
    except json.JSONEncodeError as e:
        logger.error("Failed to encode JSON for cache", extra={
            "cache_key": key,
            "error": str(e)
        })
        return False
        
    except (RedisError, ConnectionError, TimeoutError) as e:
        logger.error("Redis error during cache set", extra={
            "cache_key": key,
            "error": str(e),
            "error_type": type(e).__name__
        })
        return False
        
    except Exception as e:
        logger.error("Unexpected error during cache set", extra={
            "cache_key": key,
            "error": str(e),
            "error_type": type(e).__name__
        })
        return False


def cache_delete(key: str) -> bool:
    """Delete a key from cache. Returns True if successful."""
    if not _cache_enabled:
        return False
    
    try:
        redis_client = get_redis()
        result = redis_client.delete(key)
        logger.debug("Cache key deleted", extra={"cache_key": key, "deleted": bool(result)})
        return bool(result)
    except Exception as e:
        logger.error("Failed to delete cache key", extra={
            "cache_key": key,
            "error": str(e)
        })
        return False


def cache_exists(key: str) -> bool:
    """Check if a key exists in cache."""
    if not _cache_enabled:
        return False
    
    try:
        redis_client = get_redis()
        return bool(redis_client.exists(key))
    except Exception as e:
        logger.error("Failed to check cache key existence", extra={
            "cache_key": key,
            "error": str(e)
        })
        return False


def get_cache_stats() -> dict:
    """Get Redis cache statistics."""
    if not _cache_enabled:
        return {"enabled": False}
    
    try:
        redis_client = get_redis()
        info = redis_client.info()
        return {
            "enabled": True,
            "connected_clients": info.get("connected_clients", 0),
            "used_memory_human": info.get("used_memory_human", "0B"),
            "keyspace_hits": info.get("keyspace_hits", 0),
            "keyspace_misses": info.get("keyspace_misses", 0),
            "uptime_in_seconds": info.get("uptime_in_seconds", 0)
        }
    except Exception as e:
        logger.error("Failed to get cache stats", extra={
            "error": str(e),
            "error_type": type(e).__name__
        })
        return {"enabled": False, "error": str(e)}


def cached(ttl_seconds: int = 3600):
    """Decorator to cache function results."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Create cache key from function name and arguments
            cache_key = f"{func.__name__}:{hash(str(args) + str(sorted(kwargs.items())))}"
            
            # Try to get from cache first
            cached_result = cache_get_json(cache_key)
            if cached_result is not None:
                return cached_result
            
            # Execute function and cache result
            result = func(*args, **kwargs)
            cache_set_json(cache_key, result, ttl_seconds)
            return result
        
        return wrapper
    return decorator


