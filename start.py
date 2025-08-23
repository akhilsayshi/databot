#!/usr/bin/env python3
"""
DataBot Startup Script
Enhanced startup script with health checks and graceful initialization.
"""

import os
import sys
import signal
import time
from pathlib import Path
from typing import Optional

# Add the app directory to Python path
sys.path.insert(0, str(Path(__file__).parent))

from app.config import settings
from app.utils.logger import get_logger, setup_logging
from app.infrastructure.db import wait_for_database, init_db, check_database_health
from app.infrastructure.cache import wait_for_redis, check_redis_health
from app.health import start_health_server, mark_bot_running, mark_bot_stopped

logger = get_logger(__name__)

# Global flag for graceful shutdown
_shutdown_requested = False


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global _shutdown_requested
    logger.info(f"Received signal {signum}, initiating graceful shutdown...")
    _shutdown_requested = True


def setup_signal_handlers():
    """Setup signal handlers for graceful shutdown."""
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


def check_environment():
    """Check if required environment variables are set."""
    try:
        settings.validate()
        logger.info("Environment validation successful")
        return True
    except SystemExit:
        # Configuration validation already printed error and exited
        return False
    except Exception as e:
        logger.error(f"Environment validation failed: {str(e)}")
        print("❌ Configuration validation failed:")
        print(f"   {str(e)}")
        print("\nPlease check your environment variables or .env file.")
        return False


def create_directories():
    """Create necessary directories."""
    directories = ["logs", "data"]
    
    for dir_name in directories:
        dir_path = Path(dir_name)
        if not dir_path.exists():
            dir_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created directory: {str(dir_path)}")
            print(f"✅ Created {dir_name} directory")


def check_dependencies():
    """Check if all dependencies are available."""
    logger.info("Checking dependencies...")
    
    # Check database
    if not wait_for_database():
        logger.error("Database dependency check failed")
        return False
    
    # Check Redis
    if not wait_for_redis():
        logger.warning("Redis dependency check failed - caching will be disabled")
        # Don't fail startup if Redis is unavailable
    
    logger.info("Dependencies check completed")
    return True


def initialize_database():
    """Initialize database tables."""
    try:
        logger.info("Initializing database...")
        init_db()
        logger.info("Database initialization completed")
        return True
    except Exception as e:
        logger.error(f"Database initialization failed: {e}")
        return False


def print_startup_info():
    """Print startup information."""
    print("🎯 DataBot Startup")
    print("=" * 40)
    print(f"Environment: {settings.environment}")
    print(f"Timezone: {settings.default_timezone}")
    print(f"Log Level: {settings.log_level}")
    print(f"Database: {settings.database_url.split('@')[-1] if '@' in settings.database_url else 'local'}")
    print(f"Redis: {settings.redis_url.split('@')[-1] if '@' in settings.redis_url else 'local'}")
    print("=" * 40)


def health_check() -> bool:
    """Perform health check on all services."""
    logger.info("Performing health check...")
    
    checks = {
        "database": check_database_health(),
        "redis": check_redis_health(),
    }
    
    failed_checks = [service for service, healthy in checks.items() if not healthy]
    
    if failed_checks:
        logger.warning(f"Health check failed for: {', '.join(failed_checks)}")
        return False
    
    logger.info("Health check passed")
    return True


def main():
    """Main startup function with enhanced error handling."""
    global _shutdown_requested
    
    # Setup signal handlers
    setup_signal_handlers()
    
    # Setup logging first
    setup_logging()
    
    # Print startup info
    print_startup_info()
    
    # Check environment
    if not check_environment():
        sys.exit(1)
    
    # Create necessary directories
    create_directories()
    
    # Check dependencies
    if not check_dependencies():
        logger.error("Dependency check failed")
        sys.exit(1)
    
    # Initialize database
    if not initialize_database():
        logger.error("Database initialization failed")
        sys.exit(1)
    
    # Perform health check
    if not health_check():
        logger.warning("Health check failed, but continuing startup")
    
    print("\n🚀 Starting DataBot...")
    logger.info("DataBot startup sequence completed successfully")
    
    try:
        # Start health check server for Render
        start_health_server()
        
        # Mark bot as running
        mark_bot_running()
        
        # Import and run the bot
        from app.bot.bot import main as bot_main
        
        # Start the bot
        bot_main()
        
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (SIGINT)")
        print("\n👋 Bot stopped by user")
        
    except Exception as e:
        logger.error(f"Failed to start bot: {e}", exc_info=True)
        print(f"\n❌ Failed to start bot: {e}")
        sys.exit(1)
    
    finally:
        # Mark bot as stopped
        mark_bot_stopped()
        logger.info("DataBot shutdown complete")


if __name__ == "__main__":
    main()
