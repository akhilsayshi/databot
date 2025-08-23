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

# Patch to prevent audioop import issues
import builtins
original_import = builtins.__import__

def patched_import(name, *args, **kwargs):
    if name == 'audioop':
        # Return a dummy module to prevent the import error
        class DummyModule:
            pass
        return DummyModule()
    return original_import(name, *args, **kwargs)

builtins.__import__ = patched_import

# Global flag for graceful shutdown
_shutdown_requested = False


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    global _shutdown_requested
    print(f"Received signal {signum}, initiating graceful shutdown...")
    _shutdown_requested = True


def setup_signal_handlers():
    """Setup signal handlers for graceful shutdown."""
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


def check_environment():
    """Check if required environment variables are set."""
    required_vars = [
        "DISCORD_BOT_TOKEN",
        "YOUTUBE_API_KEY",
        "DATABASE_URL",
        "REDIS_URL"
    ]
    
    missing_vars = []
    for var in required_vars:
        if not os.getenv(var):
            missing_vars.append(var)
    
    if missing_vars:
        print("❌ Missing required environment variables:")
        for var in missing_vars:
            print(f"   - {var}")
        print("\nPlease set these variables in your .env file or environment.")
        return False
    
    print("✅ All required environment variables are set")
    return True


def create_directories():
    """Create necessary directories."""
    directories = ["logs", "data"]
    
    for dir_name in directories:
        dir_path = Path(dir_name)
        if not dir_path.exists():
            dir_path.mkdir(parents=True, exist_ok=True)
            print(f"✅ Created {dir_name} directory")


def main():
    """Main startup function with enhanced error handling."""
    global _shutdown_requested
    
    # Setup signal handlers
    setup_signal_handlers()
    
    print("🎯 DataBot Startup")
    print("=" * 40)
    
    # Check environment
    if not check_environment():
        sys.exit(1)
    
    # Create necessary directories
    create_directories()
    
    print("\n🚀 Starting DataBot...")
    
    try:
        # Import and run the bot
        from app.bot.bot import main as bot_main
        bot_main()
        
    except KeyboardInterrupt:
        print("\n👋 Bot stopped by user")
        
    except Exception as e:
        print(f"\n❌ Failed to start bot: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
