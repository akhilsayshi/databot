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
        
        if not self.discord_bot_token:
            errors.append("DISCORD_BOT_TOKEN is required")
        
        if not self.youtube_api_key:
            errors.append("YOUTUBE_API_KEY is required")
        
        if not self.database_url:
            errors.append("DATABASE_URL is required")
        
        if not self.redis_url:
            errors.append("REDIS_URL is required")
        
        if errors:
            error_msg = "\n".join([f"❌ {error}" for error in errors])
            print(f"Configuration validation failed:\n{error_msg}")
            print("\nPlease check your environment variables or .env file.")
            sys.exit(1)
    
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.environment.lower() == "production"
    
    def is_development(self) -> bool:
        """Check if running in development environment."""
        return self.environment.lower() == "development"


# Create settings instance
settings = Settings()


