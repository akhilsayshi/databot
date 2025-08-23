"""
Enhanced logging system for DataBot with structured logging and rotation.
"""

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Optional
import json
from datetime import datetime

from app.config import settings


class StructuredFormatter(logging.Formatter):
    """Custom formatter for structured logging."""
    
    def format(self, record):
        # Create structured log entry
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        
        # Add extra fields if present
        if hasattr(record, 'extra'):
            log_entry.update(record.extra)
        
        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        
        return json.dumps(log_entry)


def setup_logging():
    """Setup logging configuration."""
    # Create logs directory
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    
    # Get log level from settings
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    
    # Create formatters
    if settings.is_production():
        # Structured JSON logging for production
        formatter = StructuredFormatter()
    else:
        # Human-readable logging for development
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
    
    # Create handlers
    handlers = []
    
    # File handler with rotation
    file_handler = logging.handlers.RotatingFileHandler(
        logs_dir / "bot.log",
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5
    )
    file_handler.setFormatter(formatter)
    handlers.append(file_handler)
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    handlers.append(console_handler)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Remove existing handlers to avoid duplicates
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Add new handlers
    for handler in handlers:
        root_logger.addHandler(handler)
    
    # Set specific logger levels
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
    logging.getLogger("celery").setLevel(logging.INFO)


# Initialize logging
setup_logging()

# Create main logger
logger = logging.getLogger("databot")
bot_logger = logger


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance with the specified name."""
    return logging.getLogger(name)


def log_command(ctx, command_name: str, success: bool = True, error: str = None):
    """Log Discord command execution."""
    extra = {
        "command": command_name,
        "user_id": str(ctx.author.id),
        "user_name": ctx.author.display_name,
        "guild_id": str(ctx.guild.id) if ctx.guild else None,
        "guild_name": ctx.guild.name if ctx.guild else None,
        "channel_id": str(ctx.channel.id),
        "channel_name": ctx.channel.name,
        "success": success
    }
    
    if error:
        extra["error"] = error
        logger.warning(f"Command failed: {command_name}", extra=extra)
    else:
        logger.info(f"Command executed: {command_name}", extra=extra)
