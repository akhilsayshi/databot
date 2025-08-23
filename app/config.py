import os
import sys
from typing import Optional
from dataclasses import dataclass
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


@dataclass
class Settings:
    """Application settings with validation and sensible defaults."""
    
    # Discord configuration
    discord_bot_token: str = os.getenv("DISCORD_BOT_TOKEN", "")
    discord_guild_id: Optional[str] = os.getenv("DISCORD_GUILD_ID")

    # Database configuration
    database_url: str = os.getenv("DATABASE_URL", "postgresql+psycopg2://youtubebot:youtubebot_pass@localhost:5432/youtubebot")
    
    # Redis configuration
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # YouTube API configuration
    youtube_api_key: str = os.getenv("YOUTUBE_API_KEY", "")

    # Application configuration
    environment: str = os.getenv("ENVIRONMENT", "development")
    default_timezone: str = os.getenv("DEFAULT_TIMEZONE", "UTC")
    
    # Logging configuration
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    
    # Rate limiting configuration
    rate_limit_requests: int = int(os.getenv("RATE_LIMIT_REQUESTS", "10"))
    rate_limit_window: int = int(os.getenv("RATE_LIMIT_WINDOW", "60"))
    
    # Cache configuration
    cache_ttl: int = int(os.getenv("CACHE_TTL", "3600"))
    
    # Bot configuration
    command_prefix: str = os.getenv("COMMAND_PREFIX", "!")
    max_videos_per_user: int = int(os.getenv("MAX_VIDEOS_PER_USER", "100"))
    
    def validate(self) -> None:
        """Validate required environment variables."""
        errors = []
        
        # Check Discord bot token
        if not self.discord_bot_token:
            errors.append("DISCORD_BOT_TOKEN is required")
        elif self.discord_bot_token in ["your_discord_bot_token_here", "placeholder", ""]:
            errors.append("DISCORD_BOT_TOKEN is set to placeholder value - please set your actual Discord bot token")
        elif not self.discord_bot_token.startswith("MT") or len(self.discord_bot_token) < 50:
            errors.append("DISCORD_BOT_TOKEN appears to be invalid - should start with 'MT' and be ~59 characters")
        
        # Check YouTube API key
        if not self.youtube_api_key:
            errors.append("YOUTUBE_API_KEY is required")
        elif self.youtube_api_key in ["your_youtube_api_key_here", "placeholder", ""]:
            errors.append("YOUTUBE_API_KEY is set to placeholder value - please set your actual YouTube API key")
        elif len(self.youtube_api_key) < 30:
            errors.append("YOUTUBE_API_KEY appears to be invalid - should be ~39 characters")
        
        # Check database URL
        if not self.database_url:
            errors.append("DATABASE_URL is required")
        
        # Check Redis URL
        if not self.redis_url:
            errors.append("REDIS_URL is required")
        
        if errors:
            error_msg = "\n".join([f"❌ {error}" for error in errors])
            print(f"Configuration validation failed:\n{error_msg}")
            print("\n🔧 How to fix:")
            print("1. Go to Render Dashboard → Your Service → Environment")
            print("2. Add DISCORD_BOT_TOKEN with your actual bot token")
            print("3. Add YOUTUBE_API_KEY with your actual API key")
            print("4. Redeploy the service")
            sys.exit(1)
    
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.environment.lower() == "production"
    
    def is_development(self) -> bool:
        """Check if running in development environment."""
        return self.environment.lower() == "development"


# Create settings instance
settings = Settings()


