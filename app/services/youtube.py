from __future__ import annotations

import re
import asyncio
import aiohttp
from dataclasses import dataclass
from typing import Optional, List
from datetime import datetime

import requests
from requests.exceptions import RequestException, Timeout

from app.config import settings
from app.infrastructure.cache import cache_get_json, cache_set_json
from app.services.quota_manager import quota_manager, QuotaType
from app.utils.logger import get_logger

logger = get_logger(__name__)

# YouTube URL regex patterns - More flexible channel detection
YOUTUBE_VIDEO_REGEX = re.compile(r"(?:v=|/videos/|/shorts/|youtu\.be/)([\w-]{11})")

# Enhanced channel regex patterns to catch more URL formats
YOUTUBE_CHANNEL_REGEX = re.compile(r"""
    (?:channel/|/c/|/@|user/|youtube\.com/)?  # Optional prefixes
    ([@]?[\w\-\.]+)                           # Username/handle with optional @
""", re.VERBOSE)

# Channel ID pattern (UC followed by 22 characters)
YOUTUBE_CHANNEL_ID_REGEX = re.compile(r"UC[\w-]{22}")

# Additional patterns for various YouTube URL formats
YOUTUBE_URL_PATTERNS = [
    # Standard channel URLs
    r"youtube\.com/channel/(UC[\w-]{22})",
    r"youtube\.com/c/([\w\-\.]+)",
    r"youtube\.com/user/([\w\-\.]+)",
    r"youtube\.com/@([\w\-\.]+)",
    
    # Short URLs
    r"youtu\.be/channel/(UC[\w-]{22})",
    r"youtu\.be/c/([\w\-\.]+)",
    r"youtu\.be/user/([\w\-\.]+)",
    r"youtu\.be/@([\w\-\.]+)",
    
    # Handle variations
    r"@([\w\-\.]+)",
    r"youtube\.com/([\w\-\.]+)",
    
    # Legacy formats
    r"youtube\.com/user/([\w\-\.]+)",
    r"youtube\.com/([\w\-\.]+)",
]


@dataclass
class YouTubeVideoStats:
    video_id: str
    title: Optional[str]
    description: Optional[str]
    thumbnail_url: Optional[str]
    published_at: Optional[datetime]
    view_count: int
    like_count: int
    comment_count: int


@dataclass
class YouTubeChannelInfo:
    channel_id: str
    channel_name: str
    description: Optional[str]
    subscriber_count: Optional[int]
    video_count: Optional[int]
    view_count: Optional[int]


@dataclass
class YouTubeVideo:
    video_id: str
    title: str
    description: Optional[str]
    thumbnail_url: Optional[str]
    published_at: Optional[datetime]
    view_count: int


def parse_video_id(url: str) -> Optional[str]:
    """Extract YouTube video ID from URL"""
    if not url:
        logger.debug("Empty URL provided for video ID parsing")
        return None
    
    match = YOUTUBE_VIDEO_REGEX.search(url)
    video_id = match.group(1) if match else None
    
    if video_id:
        logger.debug("Successfully parsed video ID", extra={"video_id": video_id, "url": url})
    else:
        logger.debug("Failed to parse video ID from URL", extra={"url": url})
    
    return video_id


def parse_channel_id(url: str) -> Optional[str]:
    """Extract YouTube channel ID from URL with enhanced flexibility"""
    if not url:
        logger.debug("Empty URL provided for channel ID parsing")
        return None
    
    # Clean the URL
    url = url.strip()
    logger.debug("Parsing channel ID from URL", extra={"url": url})
    
    # First try to find a direct channel ID (UC...)
    channel_id_match = YOUTUBE_CHANNEL_ID_REGEX.search(url)
    if channel_id_match:
        channel_id = channel_id_match.group(0)
        logger.debug("Found direct channel ID", extra={"channel_id": channel_id, "url": url})
        return channel_id
    
    # Try all the enhanced patterns
    for pattern in YOUTUBE_URL_PATTERNS:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            channel_identifier = match.group(1)
            logger.debug("Found channel identifier", extra={
                "pattern": pattern,
                "identifier": channel_identifier,
                "url": url
            })
            
            # If it's already a channel ID, return it
            if channel_identifier.startswith('UC') and len(channel_identifier) == 24:
                return channel_identifier
            
            # Clean up the identifier
            if channel_identifier.startswith('@'):
                channel_identifier = channel_identifier[1:]
            
            # Remove any trailing slashes or parameters
            channel_identifier = channel_identifier.split('/')[0].split('?')[0].split('&')[0]
            
            return channel_identifier
    
    # Fallback to the original regex for backward compatibility
    match = YOUTUBE_CHANNEL_REGEX.search(url)
    if match:
        username = match.group(1)
        # Remove @ symbol if present
        if username.startswith('@'):
            username = username[1:]
        logger.debug("Found channel username (fallback)", extra={"username": username, "url": url})
        return username
    
    logger.debug("Failed to parse channel ID from URL", extra={"url": url})
    return None


def get_channel_id_from_username(username: str) -> Optional[str]:
    """Convert YouTube username/handle to channel ID using API with enhanced flexibility"""
    if not username or not settings.youtube_api_key:
        logger.warning("Missing username or YouTube API key for channel ID lookup", extra={
            "username": username,
            "has_api_key": bool(settings.youtube_api_key)
        })
        return None
    
    # Clean the username
    username = username.strip()
    
    # Remove @ symbol if present
    if username.startswith('@'):
        username = username[1:]
    
    # Remove any trailing slashes or parameters
    username = username.split('/')[0].split('?')[0].split('&')[0]
    
    logger.info("Looking up channel ID for username", extra={"username": username})
    
    # Method 1: Try the modern search approach (for handles)
    params = {
        "q": f"@{username}",
        "part": "snippet",
        "type": "channel",
        "maxResults": 1,
        "key": settings.youtube_api_key,
    }
    
    try:
        resp = requests.get("https://www.googleapis.com/youtube/v3/search", params=params, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("items", [])
            if items:
                channel_id = items[0].get("id", {}).get("channelId")
                logger.info("Found channel ID via search", extra={
                    "username": username,
                    "channel_id": channel_id
                })
                return channel_id
    except (RequestException, Timeout) as e:
        logger.error("Request failed for channel search", extra={
            "username": username,
            "error": str(e)
        })
    except Exception as e:
        logger.error("Unexpected error in channel search", extra={
            "username": username,
            "error": str(e)
        })
    
    # Method 2: Try without @ symbol
    if not username.startswith('@'):
        params = {
            "q": username,
            "part": "snippet",
            "type": "channel",
            "maxResults": 1,
            "key": settings.youtube_api_key,
        }
        
        try:
            resp = requests.get("https://www.googleapis.com/youtube/v3/search", params=params, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])
                if items:
                    channel_id = items[0].get("id", {}).get("channelId")
                    logger.info("Found channel ID via search (no @)", extra={
                        "username": username,
                        "channel_id": channel_id
                    })
                    return channel_id
        except (RequestException, Timeout) as e:
            logger.error("Request failed for channel search (no @)", extra={
                "username": username,
                "error": str(e)
            })
        except Exception as e:
            logger.error("Unexpected error in channel search (no @)", extra={
                "username": username,
                "error": str(e)
            })
    
    # Method 3: Fallback to old forUsername approach (for legacy usernames)
    params = {
        "forUsername": username,
        "part": "id",
        "key": settings.youtube_api_key,
    }
    
    try:
        resp = requests.get("https://www.googleapis.com/youtube/v3/channels", params=params, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("items", [])
            if items:
                channel_id = items[0].get("id")
                logger.info("Found channel ID via legacy lookup", extra={
                    "username": username,
                    "channel_id": channel_id
                })
                return channel_id
    except (RequestException, Timeout) as e:
        logger.error("Request failed for legacy channel lookup", extra={
            "username": username,
            "error": str(e)
        })
    except Exception as e:
        logger.error("Unexpected error in legacy channel lookup", extra={
            "username": username,
            "error": str(e)
        })
    
    logger.warning("Channel ID not found for username", extra={"username": username})
    return None


async def fetch_video_stats_async(video_id: str) -> Optional[YouTubeVideoStats]:
    """Fetch video statistics from YouTube API with quota management and caching"""
    if not video_id or not settings.youtube_api_key:
        logger.warning("Missing video ID or YouTube API key", extra={
            "video_id": video_id,
            "has_api_key": bool(settings.youtube_api_key)
        })
        return None
    
    cache_key = f"youtube:video:{video_id}"
    
    # Check cache first
    cached = cache_get_json(cache_key)
    if cached:
        try:
            # Handle datetime conversion from cache
            if cached.get("published_at"):
                cached["published_at"] = datetime.fromisoformat(cached["published_at"])
            stats = YouTubeVideoStats(**cached)
            logger.info("Retrieved video stats from cache", extra={
                "video_id": video_id,
                "view_count": stats.view_count
            })
            return stats
        except Exception as e:
            logger.warning("Failed to parse cached video stats", extra={
                "video_id": video_id,
                "error": str(e)
            })
            # If cache is corrupted, continue to fetch fresh data
    
    # Check quota and wait if needed
    if not await quota_manager.wait_if_needed(QuotaType.VIDEO_STATS):
        logger.error("YouTube API quota exceeded for video stats", extra={"video_id": video_id})
        return None
    
    logger.info("Fetching video stats from YouTube API", extra={"video_id": video_id})
    
    params = {
        "id": video_id,
        "part": "snippet,statistics",
        "key": settings.youtube_api_key,
    }
    
    success = False
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://www.googleapis.com/youtube/v3/videos", params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.error("YouTube API request failed", extra={
                        "video_id": video_id,
                        "status_code": resp.status
                    })
                    await quota_manager.record_request(QuotaType.VIDEO_STATS, success=False)
                    return None
                
                data = await resp.json()
                items = data.get("items", [])
                if not items:
                    logger.warning("Video not found in YouTube API response", extra={"video_id": video_id})
                    await quota_manager.record_request(QuotaType.VIDEO_STATS, success=False)
                    return None
                
                item = items[0]
                snippet = item.get("snippet", {})
                statistics = item.get("statistics", {})
                
                # Parse published date
                published_at = None
                if snippet.get("publishedAt"):
                    try:
                        published_at = datetime.fromisoformat(snippet["publishedAt"].replace("Z", "+00:00"))
                    except ValueError as e:
                        logger.warning("Failed to parse published date", extra={
                            "video_id": video_id,
                            "published_at": snippet["publishedAt"],
                            "error": str(e)
                        })
                
                stats = YouTubeVideoStats(
                    video_id=video_id,
                    title=snippet.get("title"),
                    description=snippet.get("description"),
                    thumbnail_url=snippet.get("thumbnails", {}).get("medium", {}).get("url"),
                    published_at=published_at,
                    view_count=int(statistics.get("viewCount", 0)),
                    like_count=int(statistics.get("likeCount", 0)),
                    comment_count=int(statistics.get("commentCount", 0))
                )
                success = True
        
                logger.info("Successfully fetched video stats", extra={
                    "video_id": video_id,
                    "title": stats.title,
                    "view_count": stats.view_count,
                    "like_count": stats.like_count
                })
                
                # Cache for 2 hours (extended from 5 minutes to reduce API calls)
                try:
                    cache_set_json(cache_key, {
                        "video_id": stats.video_id,
                        "title": stats.title,
                        "description": stats.description,
                        "thumbnail_url": stats.thumbnail_url,
                        "published_at": stats.published_at.isoformat() if stats.published_at else None,
                        "view_count": stats.view_count,
                        "like_count": stats.like_count,
                        "comment_count": stats.comment_count
                    }, 7200)  # 2 hours = 7200 seconds
                except Exception as e:
                    logger.warning("Failed to cache video stats", extra={
                        "video_id": video_id,
                        "error": str(e)
                    })
                
                return stats
        
    except asyncio.TimeoutError as e:
        logger.error("Timeout for video stats", extra={
            "video_id": video_id,
            "error": str(e)
        })
    except Exception as e:
        logger.error("Unexpected error fetching video stats", extra={
            "video_id": video_id,
            "error": str(e)
        })
    
    # Record failed request
    await quota_manager.record_request(QuotaType.VIDEO_STATS, success=False)
    return None


def fetch_video_stats(video_id: str) -> Optional[YouTubeVideoStats]:
    """Synchronous wrapper for fetch_video_stats_async - runs in Discord bot's event loop"""
    try:
        # For Discord bots, we need to schedule the coroutine in the current event loop
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Create a task and run it
            task = loop.create_task(fetch_video_stats_async(video_id))
            # This won't work in running loop, so we need a different approach
            # Fall back to sync version for now
            return _fetch_video_stats_sync(video_id)
        else:
            return loop.run_until_complete(fetch_video_stats_async(video_id))
    except RuntimeError:
        # No event loop, use sync version
        return _fetch_video_stats_sync(video_id)
    except Exception as e:
        logger.error("Error in fetch_video_stats wrapper", extra={
            "video_id": video_id,
            "error": str(e)
        })
        return _fetch_video_stats_sync(video_id)


def _fetch_video_stats_sync(video_id: str) -> Optional[YouTubeVideoStats]:
    """Synchronous version with basic quota awareness"""
    if not video_id or not settings.youtube_api_key:
        logger.warning("Missing video ID or YouTube API key", extra={
            "video_id": video_id,
            "has_api_key": bool(settings.youtube_api_key)
        })
        return None
    
    cache_key = f"youtube:video:{video_id}"
    
    # Check cache first
    cached = cache_get_json(cache_key)
    if cached:
        try:
            # Handle datetime conversion from cache
            if cached.get("published_at"):
                cached["published_at"] = datetime.fromisoformat(cached["published_at"])
            stats = YouTubeVideoStats(**cached)
            logger.info("Retrieved video stats from cache", extra={
                "video_id": video_id,
                "view_count": stats.view_count
            })
            return stats
        except Exception as e:
            logger.warning("Failed to parse cached video stats", extra={
                "video_id": video_id,
                "error": str(e)
            })
    
    # Add basic delay for rate limiting (sync version)
    import time
    time.sleep(0.2)  # 200ms delay between requests
    
    logger.info("Fetching video stats from YouTube API (sync)", extra={"video_id": video_id})
    
    params = {
        "id": video_id,
        "part": "snippet,statistics",
        "key": settings.youtube_api_key,
    }
    
    try:
        resp = requests.get("https://www.googleapis.com/youtube/v3/videos", params=params, timeout=15)
        if resp.status_code != 200:
            logger.error("YouTube API request failed (sync)", extra={
                "video_id": video_id,
                "status_code": resp.status_code
            })
            return None
        
        data = resp.json()
        items = data.get("items", [])
        if not items:
            logger.warning("Video not found in YouTube API response (sync)", extra={"video_id": video_id})
            return None
        
        item = items[0]
        snippet = item.get("snippet", {})
        statistics = item.get("statistics", {})
        
        # Parse published date
        published_at = None
        if snippet.get("publishedAt"):
            try:
                published_at = datetime.fromisoformat(snippet["publishedAt"].replace("Z", "+00:00"))
            except ValueError as e:
                logger.warning("Failed to parse published date (sync)", extra={
                    "video_id": video_id,
                    "published_at": snippet["publishedAt"],
                    "error": str(e)
                })
        
        stats = YouTubeVideoStats(
            video_id=video_id,
            title=snippet.get("title"),
            description=snippet.get("description"),
            thumbnail_url=snippet.get("thumbnails", {}).get("medium", {}).get("url"),
            published_at=published_at,
            view_count=int(statistics.get("viewCount", 0)),
            like_count=int(statistics.get("likeCount", 0)),
            comment_count=int(statistics.get("commentCount", 0))
        )
        
        logger.info("Successfully fetched video stats (sync)", extra={
            "video_id": video_id,
            "title": stats.title,
            "view_count": stats.view_count,
            "like_count": stats.like_count
        })
        
        # Cache for 2 hours
        try:
            cache_set_json(cache_key, {
                "video_id": stats.video_id,
                "title": stats.title,
                "description": stats.description,
                "thumbnail_url": stats.thumbnail_url,
                "published_at": stats.published_at.isoformat() if stats.published_at else None,
                "view_count": stats.view_count,
                "like_count": stats.like_count,
                "comment_count": stats.comment_count
            }, 7200)
        except Exception as e:
            logger.warning("Failed to cache video stats (sync)", extra={
                "video_id": video_id,
                "error": str(e)
            })
        
        return stats
        
    except (RequestException, Timeout) as e:
        logger.error("Request failed for video stats (sync)", extra={
            "video_id": video_id,
            "error": str(e)
        })
        return None
    except Exception as e:
        logger.error("Unexpected error fetching video stats (sync)", extra={
            "video_id": video_id,
            "error": str(e)
        })
        return None


def get_video_channel_id(video_id: str) -> Optional[str]:
    """Get the channel ID for a specific video"""
    if not video_id or not settings.youtube_api_key:
        logger.warning("Missing video ID or YouTube API key for channel lookup", extra={
            "video_id": video_id,
            "has_api_key": bool(settings.youtube_api_key)
        })
        return None
    
    logger.info("Fetching channel ID for video", extra={"video_id": video_id})
    
    params = {
        "id": video_id,
        "part": "snippet",
        "key": settings.youtube_api_key,
    }
    
    try:
        resp = requests.get("https://www.googleapis.com/youtube/v3/videos", params=params, timeout=15)
        if resp.status_code != 200:
            logger.error("YouTube API request failed for video channel lookup", extra={
                "video_id": video_id,
                "status_code": resp.status_code
            })
            return None
        
        data = resp.json()
        items = data.get("items", [])
        if not items:
            logger.warning("Video not found in YouTube API response for channel lookup", extra={"video_id": video_id})
            return None
        
        item = items[0]
        snippet = item.get("snippet", {})
        channel_id = snippet.get("channelId")
        
        if channel_id:
            logger.info("Successfully fetched channel ID for video", extra={
                "video_id": video_id,
                "channel_id": channel_id
            })
            return channel_id
        else:
            logger.warning("No channel ID found in video snippet", extra={"video_id": video_id})
            return None
        
    except (RequestException, Timeout) as e:
        logger.error("Request failed for video channel lookup", extra={
            "video_id": video_id,
            "error": str(e)
        })
        return None
    except Exception as e:
        logger.error("Unexpected error fetching video channel ID", extra={
            "video_id": video_id,
            "error": str(e)
        })
        return None


def fetch_channel_info_fresh(channel_id: str) -> Optional[YouTubeChannelInfo]:
    """Fetch fresh channel information from YouTube API (bypasses cache)"""
    if not channel_id or not settings.youtube_api_key:
        logger.warning("Missing channel ID or YouTube API key for fresh fetch", extra={
            "channel_id": channel_id,
            "has_api_key": bool(settings.youtube_api_key)
        })
        return None
    
    logger.info("Fetching fresh channel info from YouTube API", extra={"channel_id": channel_id})
    
    params = {
        "id": channel_id,
        "part": "snippet,statistics",
        "key": settings.youtube_api_key,
    }
    
    try:
        resp = requests.get("https://www.googleapis.com/youtube/v3/channels", params=params, timeout=15)
        if resp.status_code != 200:
            logger.error("YouTube API request failed for fresh channel fetch", extra={
                "channel_id": channel_id,
                "status_code": resp.status_code
            })
            return None
        
        data = resp.json()
        items = data.get("items", [])
        if not items:
            logger.warning("Channel not found in YouTube API response (fresh fetch)", extra={"channel_id": channel_id})
            return None
        
        item = items[0]
        snippet = item.get("snippet", {})
        statistics = item.get("statistics", {})
        
        info = YouTubeChannelInfo(
            channel_id=channel_id,
            channel_name=snippet.get("title", ""),
            description=snippet.get("description"),
            subscriber_count=int(statistics.get("subscriberCount", 0)) if statistics.get("subscriberCount") else None,
            video_count=int(statistics.get("videoCount", 0)) if statistics.get("videoCount") else None,
            view_count=int(statistics.get("viewCount", 0)) if statistics.get("viewCount") else None
        )
        
        logger.info("Successfully fetched fresh channel info", extra={
            "channel_id": channel_id,
            "channel_name": info.channel_name,
            "description_length": len(info.description) if info.description else 0
        })
        
        return info
        
    except (RequestException, Timeout) as e:
        logger.error("Request failed for fresh channel info", extra={
            "channel_id": channel_id,
            "error": str(e)
        })
        return None
    except Exception as e:
        logger.error("Unexpected error fetching fresh channel info", extra={
            "channel_id": channel_id,
            "error": str(e)
        })
        return None


def fetch_channel_info(channel_id: str) -> Optional[YouTubeChannelInfo]:
    """Fetch channel information from YouTube API with caching"""
    if not channel_id or not settings.youtube_api_key:
        logger.warning("Missing channel ID or YouTube API key", extra={
            "channel_id": channel_id,
            "has_api_key": bool(settings.youtube_api_key)
        })
        return None
    
    cache_key = f"youtube:channel:{channel_id}"
    
    # Check cache first
    cached = cache_get_json(cache_key)
    if cached:
        info = YouTubeChannelInfo(**cached)
        logger.info("Retrieved channel info from cache", extra={
            "channel_id": channel_id,
            "channel_name": info.channel_name
        })
        return info
    
    # Add basic delay for rate limiting
    import time
    time.sleep(0.2)  # 200ms delay between requests
    
    logger.info("Fetching channel info from YouTube API", extra={"channel_id": channel_id})
    
    params = {
        "id": channel_id,
        "part": "snippet,statistics",
        "key": settings.youtube_api_key,
    }
    
    try:
        resp = requests.get("https://www.googleapis.com/youtube/v3/channels", params=params, timeout=15)
        if resp.status_code != 200:
            logger.error("YouTube API request failed for channel", extra={
                "channel_id": channel_id,
                "status_code": resp.status_code
            })
            return None
        
        data = resp.json()
        items = data.get("items", [])
        if not items:
            logger.warning("Channel not found in YouTube API response", extra={"channel_id": channel_id})
            return None
        
        item = items[0]
        snippet = item.get("snippet", {})
        statistics = item.get("statistics", {})
        
        info = YouTubeChannelInfo(
            channel_id=channel_id,
            channel_name=snippet.get("title", ""),
            description=snippet.get("description"),
            subscriber_count=int(statistics.get("subscriberCount", 0)) if statistics.get("subscriberCount") else None,
            video_count=int(statistics.get("videoCount", 0)) if statistics.get("videoCount") else None,
            view_count=int(statistics.get("viewCount", 0)) if statistics.get("viewCount") else None
        )
        
        logger.info("Successfully fetched channel info", extra={
            "channel_id": channel_id,
            "channel_name": info.channel_name,
            "subscriber_count": info.subscriber_count
        })
        
        # Cache for 4 hours (extended from 10 minutes to reduce API calls)
        try:
            cache_set_json(cache_key, {
                "channel_id": info.channel_id,
                "channel_name": info.channel_name,
                "description": info.description,
                "subscriber_count": info.subscriber_count,
                "video_count": info.video_count,
                "view_count": info.view_count
            }, 14400)  # 4 hours = 14400 seconds
        except Exception as e:
            logger.warning("Failed to cache channel info", extra={
                "channel_id": channel_id,
                "error": str(e)
            })
        
        return info
        
    except (RequestException, Timeout) as e:
        logger.error("Request failed for channel info", extra={
            "channel_id": channel_id,
            "error": str(e)
        })
        return None
    except Exception as e:
        logger.error("Unexpected error fetching channel info", extra={
            "channel_id": channel_id,
            "error": str(e)
        })
        return None


def fetch_channel_videos(channel_id: str, max_results: int = 50) -> List[YouTubeVideo]:
    """Fetch recent videos from a channel"""
    if not channel_id or not settings.youtube_api_key:
        logger.warning("Missing channel ID or YouTube API key for video fetch", extra={
            "channel_id": channel_id,
            "has_api_key": bool(settings.youtube_api_key)
        })
        return []
    
    # Add basic delay for rate limiting
    import time
    time.sleep(0.2)  # 200ms delay between requests
    
    logger.info("Fetching channel videos", extra={
        "channel_id": channel_id,
        "max_results": max_results
    })
    
    params = {
        "channelId": channel_id,
        "part": "snippet",
        "order": "date",
        "maxResults": max_results,
        "type": "video",
        "key": settings.youtube_api_key,
    }
    
    try:
        resp = requests.get("https://www.googleapis.com/youtube/v3/search", params=params, timeout=15)
        if resp.status_code != 200:
            logger.error("YouTube API request failed for channel videos", extra={
                "channel_id": channel_id,
                "status_code": resp.status_code
            })
            return []
        
        data = resp.json()
        items = data.get("items", [])
        
        videos = []
        for item in items:
            snippet = item.get("snippet", {})
            video_id = item.get("id", {}).get("videoId", "")
            
            if not video_id:
                continue
            
            # Parse published date
            published_at = None
            if snippet.get("publishedAt"):
                try:
                    published_at = datetime.fromisoformat(snippet["publishedAt"].replace("Z", "+00:00"))
                except ValueError as e:
                    logger.warning("Failed to parse video published date", extra={
                        "video_id": video_id,
                        "published_at": snippet["publishedAt"],
                        "error": str(e)
                    })
            
            video = YouTubeVideo(
                video_id=video_id,
                title=snippet.get("title", ""),
                description=snippet.get("description"),
                thumbnail_url=snippet.get("thumbnails", {}).get("medium", {}).get("url"),
                published_at=published_at,
                view_count=0  # Will be fetched separately if needed
            )
            videos.append(video)
        
        logger.info("Successfully fetched channel videos", extra={
            "channel_id": channel_id,
            "video_count": len(videos)
        })
        
        return videos
        
    except (RequestException, Timeout) as e:
        logger.error("Request failed for channel videos", extra={
            "channel_id": channel_id,
            "error": str(e)
        })
        return []
    except Exception as e:
        logger.error("Unexpected error fetching channel videos", extra={
            "channel_id": channel_id,
            "error": str(e)
        })
        return []


def check_verification(channel_id: str, verification_code: str) -> bool:
    """Check if verification code exists in channel description with cache bypass"""
    if not channel_id or not verification_code:
        logger.warning("Missing channel ID or verification code", extra={
            "channel_id": channel_id,
            "has_verification_code": bool(verification_code)
        })
        return False
    
    logger.info("Checking channel verification", extra={
        "channel_id": channel_id,
        "verification_code": verification_code
    })
    
    # Clear cache for this channel to ensure fresh data
    cache_key = f"youtube:channel:{channel_id}"
    try:
        from app.infrastructure.cache import get_redis
        get_redis().delete(cache_key)
        logger.debug("Cleared channel cache for verification check", extra={"channel_id": channel_id})
    except Exception as e:
        logger.warning("Failed to clear channel cache", extra={
            "channel_id": channel_id,
            "error": str(e)
        })
    
    # Bypass cache for verification checks to get fresh description
    channel_info = fetch_channel_info_fresh(channel_id)
    if not channel_info or not channel_info.description:
        logger.warning("Channel info not found or no description", extra={"channel_id": channel_id})
        return False
    
    # Case-insensitive search for verification code
    description_lower = channel_info.description.lower()
    code_lower = verification_code.lower()
    
    is_verified = code_lower in description_lower
    logger.info("Verification check result", extra={
        "channel_id": channel_id,
        "is_verified": is_verified,
        "description_length": len(channel_info.description),
        "code_found": is_verified,
        "description_preview": channel_info.description[:100] + "..." if len(channel_info.description) > 100 else channel_info.description
    })
    
    return is_verified


def is_valid_youtube_url(url: str) -> bool:
    """Check if URL is a valid YouTube URL with enhanced flexibility"""
    if not url:
        return False
    
    # Clean the URL
    url = url.strip()
    
    # Check for video URLs
    if YOUTUBE_VIDEO_REGEX.search(url):
        return True
    
    # Check for channel URLs using the enhanced function
    if is_channel_url(url):
        return True
    
    # Additional YouTube domain checks
    youtube_domains = [
        'youtube.com',
        'youtu.be',
        'www.youtube.com',
        'm.youtube.com'
    ]
    
    url_lower = url.lower()
    for domain in youtube_domains:
        if domain in url_lower:
            return True
    
    return False


def is_video_url(url: str) -> bool:
    """Check if URL is a YouTube video URL"""
    if not url:
        return False
    
    is_video = bool(YOUTUBE_VIDEO_REGEX.search(url))
    logger.debug("YouTube video URL check", extra={"url": url, "is_video": is_video})
    return is_video


def is_channel_url(url: str) -> bool:
    """Check if URL is a YouTube channel URL with enhanced flexibility"""
    if not url:
        return False
    
    # Clean the URL
    url = url.strip()
    
    # Check for direct channel ID
    if YOUTUBE_CHANNEL_ID_REGEX.search(url):
        return True
    
    # Check all channel patterns
    for pattern in YOUTUBE_URL_PATTERNS:
        if re.search(pattern, url, re.IGNORECASE):
            return True
    
    # Fallback to original regex
    if YOUTUBE_CHANNEL_REGEX.search(url):
        return True
    
    # Additional checks for common channel indicators
    channel_indicators = [
        '/channel/',
        '/c/',
        '/user/',
        '/@',
        'youtube.com/channel',
        'youtube.com/c/',
        'youtube.com/user/',
        'youtube.com/@',
        'youtu.be/channel',
        'youtu.be/c/',
        'youtu.be/user/',
        'youtu.be/@'
    ]
    
    url_lower = url.lower()
    for indicator in channel_indicators:
        if indicator in url_lower:
            return True
    
    return False


async def fetch_channel_info_async(channel_id: str) -> Optional[YouTubeChannelInfo]:
    """Fetch channel information from YouTube API asynchronously"""
    if not channel_id or not settings.youtube_api_key:
        logger.warning("Missing channel ID or YouTube API key for async fetch", extra={
            "channel_id": channel_id,
            "has_api_key": bool(settings.youtube_api_key)
        })
        return None
    
    logger.info("Fetching channel info asynchronously from YouTube API", extra={"channel_id": channel_id})
    
    params = {
        "id": channel_id,
        "part": "snippet,statistics",
        "key": settings.youtube_api_key,
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://www.googleapis.com/youtube/v3/channels", params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.error("YouTube API request failed for async channel fetch", extra={
                        "channel_id": channel_id,
                        "status_code": resp.status
                    })
                    return None
                
                data = await resp.json()
                items = data.get("items", [])
                if not items:
                    logger.warning("Channel not found in YouTube API response (async fetch)", extra={"channel_id": channel_id})
                    return None
                
                item = items[0]
                snippet = item.get("snippet", {})
                statistics = item.get("statistics", {})
                
                info = YouTubeChannelInfo(
                    channel_id=channel_id,
                    channel_name=snippet.get("title", ""),
                    description=snippet.get("description"),
                    subscriber_count=int(statistics.get("subscriberCount", 0)) if statistics.get("subscriberCount") else None,
                    video_count=int(statistics.get("videoCount", 0)) if statistics.get("videoCount") else None,
                    view_count=int(statistics.get("viewCount", 0)) if statistics.get("viewCount") else None
                )
                
                logger.info("Successfully fetched channel info asynchronously", extra={
                    "channel_id": channel_id,
                    "channel_name": info.channel_name,
                    "description_length": len(info.description) if info.description else 0
                })
                
                return info
                
    except asyncio.TimeoutError as e:
        logger.error("Timeout for async channel info fetch", extra={
            "channel_id": channel_id,
            "error": str(e)
        })
        return None
    except Exception as e:
        logger.error("Unexpected error fetching channel info asynchronously", extra={
            "channel_id": channel_id,
            "error": str(e)
        })
        return None


async def get_channel_id_from_username_async(username: str) -> Optional[str]:
    """Convert YouTube username/handle to channel ID using API asynchronously"""
    if not username or not settings.youtube_api_key:
        logger.warning("Missing username or YouTube API key for async channel ID lookup", extra={
            "username": username,
            "has_api_key": bool(settings.youtube_api_key)
        })
        return None
    
    # Clean the username
    username = username.strip()
    
    # Remove @ symbol if present
    if username.startswith('@'):
        username = username[1:]
    
    # Remove any trailing slashes or parameters
    username = username.split('/')[0].split('?')[0].split('&')[0]
    
    logger.info("Looking up channel ID asynchronously for username", extra={"username": username})
    
    # Method 1: Try the modern search approach (for handles)
    params = {
        "q": f"@{username}",
        "part": "snippet",
        "type": "channel",
        "maxResults": 1,
        "key": settings.youtube_api_key,
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://www.googleapis.com/youtube/v3/search", params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    items = data.get("items", [])
                    if items:
                        channel_id = items[0].get("id", {}).get("channelId")
                        logger.info("Found channel ID via async search", extra={
                            "username": username,
                            "channel_id": channel_id
                        })
                        return channel_id
    except asyncio.TimeoutError as e:
        logger.error("Timeout for async channel search", extra={
            "username": username,
            "error": str(e)
        })
    except Exception as e:
        logger.error("Unexpected error in async channel search", extra={
            "username": username,
            "error": str(e)
        })
    
    # Method 2: Try without @ symbol
    if not username.startswith('@'):
        params = {
            "q": username,
            "part": "snippet",
            "type": "channel",
            "maxResults": 1,
            "key": settings.youtube_api_key,
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://www.googleapis.com/youtube/v3/search", params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        items = data.get("items", [])
                        if items:
                            channel_id = items[0].get("id", {}).get("channelId")
                            logger.info("Found channel ID via async search (no @)", extra={
                                "username": username,
                                "channel_id": channel_id
                            })
                            return channel_id
        except asyncio.TimeoutError as e:
            logger.error("Timeout for async channel search (no @)", extra={
                "username": username,
                "error": str(e)
            })
        except Exception as e:
            logger.error("Unexpected error in async channel search (no @)", extra={
                "username": username,
                "error": str(e)
            })
    
    # Method 3: Fallback to old forUsername approach (for legacy usernames)
    params = {
        "forUsername": username,
        "part": "id",
        "key": settings.youtube_api_key,
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://www.googleapis.com/youtube/v3/channels", params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    items = data.get("items", [])
                    if items:
                        channel_id = items[0].get("id")
                        logger.info("Found channel ID via async legacy lookup", extra={
                            "username": username,
                            "channel_id": channel_id
                        })
                        return channel_id
    except asyncio.TimeoutError as e:
        logger.error("Timeout for async legacy channel lookup", extra={
            "username": username,
            "error": str(e)
        })
    except Exception as e:
        logger.error("Unexpected error in async legacy channel lookup", extra={
            "username": username,
            "error": str(e)
        })
    
    logger.warning("Channel ID not found for username (async)", extra={"username": username})
    return None


