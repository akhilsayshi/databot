from __future__ import annotations

import time
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
from dataclasses import dataclass, field
from enum import Enum

from app.infrastructure.cache import cache_get_json, cache_set_json, get_redis
from app.utils.logger import get_logger

logger = get_logger(__name__)


class QuotaType(Enum):
    """YouTube API quota types and their costs"""
    VIDEO_STATS = 1          # videos.list with snippet,statistics
    CHANNEL_INFO = 1         # channels.list with snippet,statistics  
    CHANNEL_SEARCH = 100     # search.list for channels
    CHANNEL_VIDEOS = 100     # search.list for channel videos
    VIDEO_SEARCH = 100       # search.list for videos


@dataclass
class QuotaUsage:
    """Track quota usage over time"""
    total_quota_used: int = 0
    requests_made: int = 0
    last_reset_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    daily_quota_used: int = 0
    hourly_quota_used: int = 0
    errors_count: int = 0
    rate_limited_count: int = 0


@dataclass
class QuotaLimits:
    """YouTube API quota limits"""
    DAILY_QUOTA_LIMIT = 10000  # YouTube API daily quota
    HOURLY_QUOTA_LIMIT = 1000  # Self-imposed hourly limit
    REQUESTS_PER_MINUTE = 300  # Self-imposed rate limit
    REQUESTS_PER_SECOND = 5    # Self-imposed burst limit
    
    # Warning thresholds
    DAILY_WARNING_THRESHOLD = 8000   # 80% of daily limit
    HOURLY_WARNING_THRESHOLD = 800   # 80% of hourly limit


class QuotaManager:
    """Manages YouTube API quota usage with intelligent rate limiting"""
    
    def __init__(self):
        self.redis = get_redis()
        self.limits = QuotaLimits()
        self._request_times: list[float] = []
        self._last_request_time = 0.0
        
    def _get_cache_key(self, timeframe: str) -> str:
        """Get cache key for quota tracking"""
        return f"youtube_quota:{timeframe}"
    
    def _get_current_usage(self, timeframe: str) -> QuotaUsage:
        """Get current quota usage for timeframe"""
        cache_key = self._get_cache_key(timeframe)
        cached = cache_get_json(cache_key)
        
        if cached:
            # Convert datetime strings back to datetime objects
            if cached.get("last_reset_time"):
                cached["last_reset_time"] = datetime.fromisoformat(cached["last_reset_time"])
            return QuotaUsage(**cached)
        
        return QuotaUsage()
    
    def _save_usage(self, usage: QuotaUsage, timeframe: str, ttl: int) -> None:
        """Save quota usage to cache"""
        cache_key = self._get_cache_key(timeframe)
        
        # Convert datetime to string for JSON serialization
        usage_dict = {
            "total_quota_used": usage.total_quota_used,
            "requests_made": usage.requests_made,
            "last_reset_time": usage.last_reset_time.isoformat(),
            "daily_quota_used": usage.daily_quota_used,
            "hourly_quota_used": usage.hourly_quota_used,
            "errors_count": usage.errors_count,
            "rate_limited_count": usage.rate_limited_count
        }
        
        cache_set_json(cache_key, usage_dict, ttl)
    
    def _should_reset_timeframe(self, usage: QuotaUsage, timeframe: str) -> bool:
        """Check if timeframe should be reset"""
        now = datetime.now(timezone.utc)
        
        if timeframe == "daily":
            return now.date() > usage.last_reset_time.date()
        elif timeframe == "hourly":
            return now.hour != usage.last_reset_time.hour
        
        return False
    
    def _reset_timeframe_if_needed(self, timeframe: str) -> QuotaUsage:
        """Reset quota usage if timeframe has passed"""
        usage = self._get_current_usage(timeframe)
        
        if self._should_reset_timeframe(usage, timeframe):
            logger.info(f"Resetting {timeframe} quota usage", extra={
                "previous_usage": usage.total_quota_used,
                "requests_made": usage.requests_made
            })
            
            # Reset usage for new timeframe
            usage = QuotaUsage()
            
            # Save reset usage
            ttl = 86400 if timeframe == "daily" else 3600  # 24h or 1h
            self._save_usage(usage, timeframe, ttl)
        
        return usage
    
    async def check_quota_availability(self, quota_cost: int) -> tuple[bool, str]:
        """Check if we can make a request with given quota cost"""
        
        # Reset timeframes if needed
        daily_usage = self._reset_timeframe_if_needed("daily")
        hourly_usage = self._reset_timeframe_if_needed("hourly")
        
        # Check daily limit
        if daily_usage.daily_quota_used + quota_cost > self.limits.DAILY_QUOTA_LIMIT:
            return False, f"Daily quota limit exceeded ({daily_usage.daily_quota_used}/{self.limits.DAILY_QUOTA_LIMIT})"
        
        # Check hourly limit
        if hourly_usage.hourly_quota_used + quota_cost > self.limits.HOURLY_QUOTA_LIMIT:
            return False, f"Hourly quota limit exceeded ({hourly_usage.hourly_quota_used}/{self.limits.HOURLY_QUOTA_LIMIT})"
        
        # Check rate limiting
        now = time.time()
        
        # Remove old request times (older than 1 minute)
        self._request_times = [t for t in self._request_times if now - t < 60]
        
        # Check requests per minute
        if len(self._request_times) >= self.limits.REQUESTS_PER_MINUTE:
            return False, f"Rate limit exceeded ({len(self._request_times)}/{self.limits.REQUESTS_PER_MINUTE} requests per minute)"
        
        # Check requests per second (burst protection)
        recent_requests = [t for t in self._request_times if now - t < 1.0]
        if len(recent_requests) >= self.limits.REQUESTS_PER_SECOND:
            return False, f"Burst limit exceeded ({len(recent_requests)}/{self.limits.REQUESTS_PER_SECOND} requests per second)"
        
        return True, "OK"
    
    async def get_required_delay(self) -> float:
        """Calculate required delay before next request"""
        now = time.time()
        
        # Minimum delay between requests (200ms)
        min_delay = 0.2
        time_since_last = now - self._last_request_time
        
        if time_since_last < min_delay:
            return min_delay - time_since_last
        
        # Additional delay if we're approaching rate limits
        self._request_times = [t for t in self._request_times if now - t < 60]
        
        if len(self._request_times) > self.limits.REQUESTS_PER_MINUTE * 0.8:  # 80% of limit
            return 1.0  # 1 second delay
        elif len(self._request_times) > self.limits.REQUESTS_PER_MINUTE * 0.6:  # 60% of limit
            return 0.5  # 500ms delay
        
        return 0.0
    
    async def record_request(self, quota_type: QuotaType, success: bool = True) -> None:
        """Record an API request and its quota usage"""
        now = time.time()
        quota_cost = quota_type.value
        
        # Record request time
        self._request_times.append(now)
        self._last_request_time = now
        
        # Update daily usage
        daily_usage = self._get_current_usage("daily")
        daily_usage.total_quota_used += quota_cost
        daily_usage.daily_quota_used += quota_cost
        daily_usage.requests_made += 1
        
        if not success:
            daily_usage.errors_count += 1
        
        self._save_usage(daily_usage, "daily", 86400)
        
        # Update hourly usage
        hourly_usage = self._get_current_usage("hourly")
        hourly_usage.total_quota_used += quota_cost
        hourly_usage.hourly_quota_used += quota_cost
        hourly_usage.requests_made += 1
        
        if not success:
            hourly_usage.errors_count += 1
        
        self._save_usage(hourly_usage, "hourly", 3600)
        
        # Log quota usage
        logger.info("YouTube API request recorded", extra={
            "quota_type": quota_type.name,
            "quota_cost": quota_cost,
            "success": success,
            "daily_quota_used": daily_usage.daily_quota_used,
            "hourly_quota_used": hourly_usage.hourly_quota_used,
            "requests_made_today": daily_usage.requests_made
        })
        
        # Check warning thresholds
        await self._check_warning_thresholds(daily_usage, hourly_usage)
    
    async def _check_warning_thresholds(self, daily_usage: QuotaUsage, hourly_usage: QuotaUsage) -> None:
        """Check if we're approaching quota limits and log warnings"""
        
        # Daily quota warning
        if daily_usage.daily_quota_used >= self.limits.DAILY_WARNING_THRESHOLD:
            if daily_usage.daily_quota_used < self.limits.DAILY_QUOTA_LIMIT:
                logger.warning("Daily quota warning threshold reached", extra={
                    "quota_used": daily_usage.daily_quota_used,
                    "quota_limit": self.limits.DAILY_QUOTA_LIMIT,
                    "percentage": round((daily_usage.daily_quota_used / self.limits.DAILY_QUOTA_LIMIT) * 100, 1)
                })
        
        # Hourly quota warning  
        if hourly_usage.hourly_quota_used >= self.limits.HOURLY_WARNING_THRESHOLD:
            if hourly_usage.hourly_quota_used < self.limits.HOURLY_QUOTA_LIMIT:
                logger.warning("Hourly quota warning threshold reached", extra={
                    "quota_used": hourly_usage.hourly_quota_used,
                    "quota_limit": self.limits.HOURLY_QUOTA_LIMIT,
                    "percentage": round((hourly_usage.hourly_quota_used / self.limits.HOURLY_QUOTA_LIMIT) * 100, 1)
                })
    
    async def get_quota_status(self) -> Dict[str, Any]:
        """Get current quota status"""
        daily_usage = self._reset_timeframe_if_needed("daily")
        hourly_usage = self._reset_timeframe_if_needed("hourly")
        
        now = time.time()
        self._request_times = [t for t in self._request_times if now - t < 60]
        
        return {
            "daily": {
                "used": daily_usage.daily_quota_used,
                "limit": self.limits.DAILY_QUOTA_LIMIT,
                "remaining": self.limits.DAILY_QUOTA_LIMIT - daily_usage.daily_quota_used,
                "percentage": round((daily_usage.daily_quota_used / self.limits.DAILY_QUOTA_LIMIT) * 100, 1),
                "requests_made": daily_usage.requests_made,
                "errors": daily_usage.errors_count
            },
            "hourly": {
                "used": hourly_usage.hourly_quota_used,
                "limit": self.limits.HOURLY_QUOTA_LIMIT,
                "remaining": self.limits.HOURLY_QUOTA_LIMIT - hourly_usage.hourly_quota_used,
                "percentage": round((hourly_usage.hourly_quota_used / self.limits.HOURLY_QUOTA_LIMIT) * 100, 1),
                "requests_made": hourly_usage.requests_made,
                "errors": hourly_usage.errors_count
            },
            "rate_limiting": {
                "requests_last_minute": len(self._request_times),
                "limit_per_minute": self.limits.REQUESTS_PER_MINUTE,
                "requests_last_second": len([t for t in self._request_times if now - t < 1.0]),
                "limit_per_second": self.limits.REQUESTS_PER_SECOND
            }
        }
    
    async def wait_if_needed(self, quota_type: QuotaType) -> bool:
        """Wait if needed before making request. Returns True if request can proceed."""
        quota_cost = quota_type.value
        
        # Check quota availability
        can_proceed, reason = await self.check_quota_availability(quota_cost)
        
        if not can_proceed:
            logger.warning("YouTube API request blocked", extra={
                "quota_type": quota_type.name,
                "quota_cost": quota_cost,
                "reason": reason
            })
            return False
        
        # Apply intelligent delay
        delay = await self.get_required_delay()
        if delay > 0:
            logger.debug("Applying API rate limiting delay", extra={
                "delay_seconds": delay,
                "quota_type": quota_type.name
            })
            await asyncio.sleep(delay)
        
        return True


# Global quota manager instance
quota_manager = QuotaManager()
