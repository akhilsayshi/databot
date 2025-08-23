from __future__ import annotations

from celery import Celery

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Initialize Celery app
celery_app = Celery(
    "salmonbot",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.tasks.refresh_stats",
        "app.tasks.monthly_reports",
        "app.tasks.automatic_tracking",
    ],
)

# Configure task routing
celery_app.conf.task_routes = {
    "app.tasks.refresh_stats.*": {"queue": "stats"},
    "app.tasks.monthly_reports.*": {"queue": "reports"},
    "app.tasks.automatic_tracking.*": {"queue": "auto"},
}

# Configure periodic tasks
celery_app.conf.beat_schedule = {
    "refresh-all-every-2h": {
        "task": "app.tasks.refresh_stats.refresh_all",
        "schedule": 2 * 60 * 60,  # 2 hours in seconds
    },
    "sync-automatic-channels-every-6h": {
        "task": "app.tasks.refresh_stats.sync_automatic_channels",
        "schedule": 6 * 60 * 60,  # 6 hours in seconds
    },
    "sync-new-videos-every-4h": {
        "task": "app.tasks.automatic_tracking.sync_new_videos_from_channels",
        "schedule": 4 * 60 * 60,  # 4 hours in seconds
    },
    "check-monthly-reports-daily": {
        "task": "app.tasks.monthly_reports.trigger_monthly_reports_if_needed",
        "schedule": 24 * 60 * 60,  # Daily at midnight
    },
    "generate-monthly-summary-daily": {
        "task": "app.tasks.automatic_tracking.generate_monthly_summary",
        "schedule": 24 * 60 * 60,  # Daily at midnight
    },
    "cleanup-old-data-weekly": {
        "task": "app.tasks.automatic_tracking.cleanup_old_data",
        "schedule": 7 * 24 * 60 * 60,  # Weekly
    }
}

# Configure Celery settings
celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone=settings.default_timezone,
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30 * 60,  # 30 minutes
    task_soft_time_limit=25 * 60,  # 25 minutes
)

logger.info("Celery app configured", extra={
    "broker_url": settings.redis_url.split("@")[-1] if "@" in settings.redis_url else "local",
    "timezone": settings.default_timezone,
    "environment": settings.environment
})


