from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import List, Optional
import asyncio

from sqlalchemy import select, and_, func
from sqlalchemy.orm import joinedload

from app.infrastructure.db import session_scope
from app.models import User, Channel, Video, MonthlyView
from app.services.youtube import fetch_video_stats, fetch_channel_videos
from app.tasks.celery_app import celery_app
from app.utils.logger import bot_logger


@celery_app.task
def sync_new_videos_from_channels():
    """Automatically sync new videos from all verified channels"""
    with session_scope() as session:
        # Get all verified channels
        channels = session.execute(
            select(Channel).where(
                and_(
                    Channel.is_verified == True,
                    Channel.is_active == True
                )
            )
        ).scalars().all()
        
        total_new_videos = 0
        total_updated_videos = 0
        errors = 0
        
        for channel in channels:
            try:
                # Fetch recent videos from channel
                channel_videos = fetch_channel_videos(channel.channel_id, max_results=50)
                if not channel_videos:
                    continue
                
                # Get existing videos for this channel
                existing_videos = session.execute(
                    select(Video).where(Video.channel_id == channel.id)
                ).scalars().all()
                existing_video_ids = {v.video_id for v in existing_videos}
                
                # Find new videos
                new_videos = [v for v in channel_videos if v.video_id not in existing_video_ids]
                
                # Add new videos
                for video_data in new_videos:
                    try:
                        video = Video(
                            user_id=channel.user_id,
                            channel_id=channel.id,
                            video_id=video_data.video_id,
                            url=f"https://www.youtube.com/watch?v={video_data.video_id}",
                            title=video_data.title,
                            description=video_data.description,
                            thumbnail_url=video_data.thumbnail_url,
                            published_at=video_data.published_at,
                            last_view_count=video_data.view_count,
                            last_updated_at=datetime.now(timezone.utc),
                            created_at=datetime.now(timezone.utc)
                        )
                        session.add(video)
                        total_new_videos += 1
                        
                        bot_logger.info(f"Added new video {video_data.video_id} from channel {channel.channel_name}")
                        
                    except Exception as e:
                        errors += 1
                        bot_logger.error(f"Error adding video {video_data.video_id}: {e}")
                        continue
                
                # Update existing videos with current stats
                for video in existing_videos:
                    try:
                        stats = fetch_video_stats(video.video_id)
                        if stats:
                            # Calculate view change
                            view_change = stats.view_count - video.last_view_count
                            
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
                                # Add only incremental view change to monthly total
                                if view_change > 0:
                                    monthly_view.views += view_change
                                monthly_view.updated_at = now
                            else:
                                # Create new monthly view record starting from current baseline
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
                            
                            total_updated_videos += 1
                            
                    except Exception as e:
                        errors += 1
                        bot_logger.error(f"Error updating video {video.video_id}: {e}")
                        continue
                
                # Update channel last sync time
                channel.last_sync_at = datetime.now(timezone.utc)
                
            except Exception as e:
                errors += 1
                bot_logger.error(f"Error syncing channel {channel.channel_id}: {e}")
                continue
        
        bot_logger.info(f"Auto sync complete: {total_new_videos} new videos, {total_updated_videos} updated, {errors} errors")
        return {
            "new_videos": total_new_videos,
            "updated_videos": total_updated_videos,
            "errors": errors
        }


@celery_app.task
def generate_monthly_summary():
    """Generate monthly summary for all users at the end of each month"""
    now = datetime.now(timezone.utc)
    
    # Check if it's the last day of the month
    tomorrow = now + timedelta(days=1)
    if tomorrow.day != 1:
        return {"message": "Not end of month"}
    
    with session_scope() as session:
        # Get all users with tracked videos
        users = session.execute(
            select(User).distinct().join(Video)
        ).scalars().all()
        
        summaries = []
        
        for user in users:
            try:
                # Get current month's data
                current_month_views = session.execute(
                    select(func.sum(MonthlyView.views)).where(
                        and_(
                            MonthlyView.user_id == user.id,
                            MonthlyView.year == now.year,
                            MonthlyView.month == now.month
                        )
                    )
                ).scalar() or 0
                
                # Get previous month's data for comparison
                if now.month == 1:
                    prev_month = 12
                    prev_year = now.year - 1
                else:
                    prev_month = now.month - 1
                    prev_year = now.year
                
                prev_month_views = session.execute(
                    select(func.sum(MonthlyView.views)).where(
                        and_(
                            MonthlyView.user_id == user.id,
                            MonthlyView.year == prev_year,
                            MonthlyView.month == prev_month
                        )
                    )
                ).scalar() or 0
                
                # Calculate growth
                growth = current_month_views - prev_month_views
                growth_percent = (growth / prev_month_views * 100) if prev_month_views > 0 else 0
                
                # Get video count
                video_count = session.execute(
                    select(func.count(Video.id)).where(Video.user_id == user.id)
                ).scalar() or 0
                
                summaries.append({
                    "user_id": user.discord_user_id,
                    "username": user.discord_username,
                    "current_views": current_month_views,
                    "prev_views": prev_month_views,
                    "growth": growth,
                    "growth_percent": growth_percent,
                    "video_count": video_count
                })
                
            except Exception as e:
                bot_logger.error(f"Error generating summary for user {user.id}: {e}")
                continue
        
        bot_logger.info(f"Generated monthly summaries for {len(summaries)} users")
        return {"summaries": summaries}


@celery_app.task
def cleanup_old_data():
    """Clean up old data to keep the database manageable"""
    with session_scope() as session:
        # Remove monthly views older than 2 years
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=730)
        cutoff_year = cutoff_date.year
        cutoff_month = cutoff_date.month
        
        deleted_count = session.execute(
            select(func.count(MonthlyView.id)).where(
                and_(
                    MonthlyView.year < cutoff_year,
                    MonthlyView.month < cutoff_month
                )
            )
        ).scalar() or 0
        
        if deleted_count > 0:
            session.execute(
                MonthlyView.__table__.delete().where(
                    and_(
                        MonthlyView.year < cutoff_year,
                        MonthlyView.month < cutoff_month
                    )
                )
            )
            bot_logger.info(f"Cleaned up {deleted_count} old monthly view records")
        
        return {"cleaned_records": deleted_count}
