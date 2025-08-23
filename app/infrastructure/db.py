from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional
import time

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.exc import SQLAlchemyError, OperationalError
from sqlalchemy.pool import QueuePool

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


class Base(DeclarativeBase):
    pass


def create_database_engine():
    """Create database engine with optimized settings for production."""
    # Connection pool settings
    pool_settings = {
        "poolclass": QueuePool,
        "pool_size": 10,
        "max_overflow": 20,
        "pool_pre_ping": True,
        "pool_recycle": 3600,  # Recycle connections after 1 hour
        "pool_timeout": 30,    # Wait up to 30 seconds for a connection
    }
    
    # Development settings
    if settings.is_development():
        pool_settings.update({
            "echo": True,
            "pool_size": 5,
            "max_overflow": 10,
        })
    
    try:
        engine = create_engine(
            settings.database_url,
            future=True,
            **pool_settings
        )
        
        logger.info("Database engine created successfully", extra={
            "database_url": settings.database_url.split("@")[-1] if "@" in settings.database_url else "local",
            "environment": settings.environment,
            "pool_size": pool_settings["pool_size"],
            "max_overflow": pool_settings["max_overflow"]
        })
        
        return engine
        
    except Exception as e:
        logger.error("Failed to create database engine", extra={
            "error": str(e),
            "error_type": type(e).__name__,
            "database_url": settings.database_url.split("@")[-1] if "@" in settings.database_url else "local"
        })
        raise


# Create database engine
engine = create_database_engine()

# Create session factory
SessionLocal = sessionmaker(
    bind=engine, 
    autoflush=False, 
    autocommit=False, 
    expire_on_commit=False, 
    future=True
)


def check_database_health() -> bool:
    """Check if database is healthy and accessible."""
    try:
        with engine.connect() as connection:
            result = connection.execute(text("SELECT 1"))
            result.fetchone()
            return True
    except Exception as e:
        logger.error("Database health check failed", extra={
            "error": str(e),
            "error_type": type(e).__name__
        })
        return False


def wait_for_database(max_retries: int = 30, retry_delay: float = 2.0) -> bool:
    """Wait for database to become available."""
    logger.info("Waiting for database to become available...")
    
    for attempt in range(max_retries):
        if check_database_health():
            logger.info("Database is available", extra={"attempts": attempt + 1})
            return True
        
        logger.warning(f"Database not available, retrying in {retry_delay}s...", extra={
            "attempt": attempt + 1,
            "max_retries": max_retries
        })
        time.sleep(retry_delay)
    
    logger.error("Database failed to become available", extra={"max_retries": max_retries})
    return False


@contextmanager
def session_scope() -> Iterator:
    """Provide a transactional scope around a series of operations."""
    session = SessionLocal()
    try:
        logger.debug("Database session started")
        yield session
        session.commit()
        logger.debug("Database session committed")
    except SQLAlchemyError as e:
        session.rollback()
        logger.error("Database session rolled back", extra={
            "error": str(e),
            "error_type": type(e).__name__
        })
        raise
    except Exception as e:
        session.rollback()
        logger.error("Unexpected error in database session", extra={
            "error": str(e),
            "error_type": type(e).__name__
        })
        raise
    finally:
        session.close()
        logger.debug("Database session closed")


def get_session() -> SessionLocal:
    """Get a database session."""
    return SessionLocal()


def init_db():
    """Initialize database tables."""
    try:
        # Wait for database to be available
        if not wait_for_database():
            raise Exception("Database is not available")
        
        from app.models import Base
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables created successfully")
        
        # Verify tables were created
        with engine.connect() as connection:
            tables = connection.execute(text("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public'
            """)).fetchall()
            
            table_names = [row[0] for row in tables]
            logger.info("Database tables verified", extra={"tables": table_names})
            
    except Exception as e:
        logger.error("Failed to initialize database", extra={
            "error": str(e),
            "error_type": type(e).__name__
        })
        raise


def drop_db():
    """Drop all database tables."""
    try:
        from app.models import Base
        Base.metadata.drop_all(bind=engine)
        logger.info("Database tables dropped successfully")
    except Exception as e:
        logger.error("Failed to drop database tables", extra={
            "error": str(e),
            "error_type": type(e).__name__
        })
        raise


def get_database_stats() -> dict:
    """Get database connection pool statistics."""
    try:
        pool = engine.pool
        return {
            "pool_size": pool.size(),
            "checked_in": pool.checkedin(),
            "checked_out": pool.checkedout(),
            "overflow": pool.overflow(),
            "invalid": pool.invalid()
        }
    except Exception as e:
        logger.error("Failed to get database stats", extra={
            "error": str(e),
            "error_type": type(e).__name__
        })
        return {}