from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from sqlalchemy import select, and_

from app.infrastructure.db import session_scope
from app.models import User, Channel, Video, MonthlyView
from app.services.youtube import fetch_video_stats, fetch_channel_videos
from app.tasks.celery_app import celery_app
from app.utils.logger import bot_logger


@celery_app.task
def refresh_video_stats():
    """Refresh video statistics for all tracked videos"""
    with session_scope() as session:
        # Get all active videos
        videos = session.execute(
            select(Video).where(Video.is_active == True)
        ).scalars().all()
        
        updated_count = 0
        error_count = 0
        
        for video in videos:
            try:
                # Fetch current stats
                stats = fetch_video_stats(video.video_id)
                if not stats:
                    continue
                
                # Update video record
                video.last_view_count = stats.view_count
                video.last_updated_at = datetime.now(timezone.utc)
                
                # Update or create monthly view record
                now = datetime.now(timezone.utc)
                monthly_view = session.execute(
                    select(MonthlyView).where(
                        and_(
                            MonthlyView.video_id == video.id,
                            MonthlyView.year == now.year,
                            MonthlyView.month == now.month
                        )
                    )
                ).scalar_one_or_none()
                
                if monthly_view:
                    # Calculate incremental view change since last update
                    views_change = stats.view_count - video.last_view_count
                    if views_change > 0:
                        monthly_view.views += views_change  # Add only new views to monthly total
                    monthly_view.updated_at = now
                else:
                    # Create new monthly view record starting from current baseline
                    # Only track views gained after this point
                    monthly_view = MonthlyView(
                        user_id=video.user_id,
                        video_id=video.id,
                        year=now.year,
                        month=now.month,
                        views=0,  # Start at 0 - only track incremental views
                        views_change=0,
                        updated_at=now
                    )
                    session.add(monthly_view)
                
                updated_count += 1
                
            except Exception as e:
                bot_logger.error(f"Error updating video {video.video_id}: {e}")
                error_count += 1
                continue
        
        bot_logger.info(f"Updated {updated_count} videos, {error_count} errors")
        return {"updated": updated_count, "errors": error_count}


@celery_app.task
def sync_automatic_channels():
    """Sync videos from automatic channels"""
    with session_scope() as session:
        # Get all verified automatic channels
        channels = session.execute(
            select(Channel).where(
                and_(
                    Channel.is_verified == True,
                    Channel.verification_mode == "automatic",
                    Channel.is_active == True
                )
            )
        ).scalars().all()
        
        synced_count = 0
        error_count = 0
        
        for channel in channels:
            try:
                # Fetch recent videos from channel
                videos = fetch_channel_videos(channel.channel_id, max_results=20)
                
                for video_info in videos:
                    # Check if video already exists
                    existing_video = session.execute(
                        select(Video).where(
                            and_(
                                Video.user_id == channel.user_id,
                                Video.video_id == video_info.video_id
                            )
                        )
                    ).scalar_one_or_none()
                    
                    if existing_video:
                        continue
                    
                    # Fetch detailed video stats
                    stats = fetch_video_stats(video_info.video_id)
                    if not stats:
                        continue
                    
                    # Create video record
                    video = Video(
                        user_id=channel.user_id,
                        channel_id=channel.id,
                        video_id=video_info.video_id,
                        url=f"https://www.youtube.com/watch?v={video_info.video_id}",
                        title=stats.title,
                        description=stats.description,
                        thumbnail_url=stats.thumbnail_url,
                        published_at=stats.published_at,
                        last_view_count=stats.view_count,
                        created_at=datetime.now(timezone.utc),
                    )
                    session.add(video)
                    session.flush()
                    
                    # Create initial monthly view record
                    now = datetime.now(timezone.utc)
                    monthly_view = MonthlyView(
                        user_id=channel.user_id,
                        video_id=video.id,
                        year=now.year,
                        month=now.month,
                        views=stats.view_count,
                        updated_at=now
                    )
                    session.add(monthly_view)
                
                # Update last sync time
                channel.last_sync_at = datetime.now(timezone.utc)
                synced_count += 1
                
            except Exception as e:
                bot_logger.error(f"Error syncing channel {channel.channel_id}: {e}")
                error_count += 1
                continue
        
        bot_logger.info(f"Synced {synced_count} channels, {error_count} errors")
        return {"synced": synced_count, "errors": error_count}


