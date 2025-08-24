from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
import asyncio

from sqlalchemy import select, and_, func
from sqlalchemy.orm import joinedload

from app.infrastructure.db import session_scope
from app.models import User, Video, MonthlyView, Channel
from app.services.youtube import fetch_video_stats, fetch_channel_videos
from app.tasks.celery_app import celery_app
from app.utils.logger import bot_logger


@celery_app.task
def trigger_monthly_reports_if_needed():
    """Check if it's end of month and trigger monthly reports for all users"""
    now = datetime.now(timezone.utc)
    
    # Check if it's the last day of the month (or first day of next month before 6 AM)
    tomorrow = now + timedelta(days=1)
    is_end_of_month = (
        tomorrow.day == 1 and 
        now.hour >= 0 and 
        now.hour < 6
    )
    
    if is_end_of_month:
        bot_logger.info("End of month detected, triggering monthly reports")
        generate_monthly_reports_for_all_users.delay()
    else:
        bot_logger.debug("Not end of month, skipping monthly reports")


@celery_app.task
def generate_monthly_reports_for_all_users():
    """Generate comprehensive monthly reports for all users"""
    with session_scope() as session:
        # Get all users with tracked videos
        users = session.execute(
            select(User).distinct().join(Video)
        ).scalars().all()
        
        bot_logger.info(f"Generating monthly reports for {len(users)} users")
        
        for user in users:
            try:
                generate_user_monthly_report.delay(user.id)
            except Exception as e:
                bot_logger.error(f"Error queuing monthly report for user {user.id}: {e}")
                continue


@celery_app.task
def generate_user_monthly_report(user_id: int):
    """Generate comprehensive monthly report for a specific user"""
    with session_scope() as session:
        user = session.execute(
            select(User).where(User.id == user_id)
        ).scalar_one_or_none()
        
        if not user:
            bot_logger.error(f"User {user_id} not found")
            return
        
        # Get the previous month
        now = datetime.now(timezone.utc)
        if now.month == 1:
            report_month = 12
            report_year = now.year - 1
        else:
            report_month = now.month - 1
            report_year = now.year
        
        bot_logger.info(f"Generating monthly report for user {user.discord_username} - {report_month}/{report_year}")
        
        # Get all user's videos
        videos = session.execute(
            select(Video).where(Video.user_id == user.id)
        ).scalars().all()
        
        if not videos:
            bot_logger.info(f"No videos found for user {user.discord_username}")
            return
        
        # Fetch final stats for all videos
        final_stats = []
        total_views = 0
        total_likes = 0
        total_videos = len(videos)
        
        for video in videos:
            try:
                # Fetch final stats for the month
                stats = fetch_video_stats(video.video_id)
                if stats:
                    # Update video with final stats
                    video.last_view_count = stats.view_count
                    video.last_updated_at = now
                    
                    # Update or create final monthly view record
                    monthly_view = session.execute(
                        select(MonthlyView).where(
                            and_(
                                MonthlyView.user_id == user.id,
                                MonthlyView.video_id == video.id,
                                MonthlyView.year == report_year,
                                MonthlyView.month == report_month
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
                        # Create monthly view record starting from current baseline
                        monthly_view = MonthlyView(
                            user_id=user.id,
                            video_id=video.id,
                            year=report_year,
                            month=report_month,
                            views=0,  # Start at 0 - only track incremental views
                            views_change=0,
                            updated_at=now
                        )
                        session.add(monthly_view)
                    
                    final_stats.append({
                        'video': video,
                        'stats': stats,
                        'monthly_views': monthly_view.views,
                        'views_change': monthly_view.views_change
                    })
                    
                    total_views += stats.view_count
                    total_likes += stats.like_count
                    
            except Exception as e:
                bot_logger.error(f"Error fetching final stats for video {video.video_id}: {e}")
                continue
        
        # Commit all updates
        session.commit()
        
        # Generate report summary
        report_summary = {
            'user_id': user.id,
            'discord_user_id': user.discord_user_id,
            'discord_username': user.discord_username,
            'month': report_month,
            'year': report_year,
            'total_views': total_views,
            'total_likes': total_likes,
            'total_videos': total_videos,
            'videos_with_stats': len(final_stats),
            'top_performers': [],
            'monthly_growth': 0
        }
        
        # Calculate top performers
        if final_stats:
            top_performers = sorted(final_stats, key=lambda x: x['monthly_views'], reverse=True)[:5]
            report_summary['top_performers'] = [
                {
                    'title': item['video'].title or f"Video {item['video'].video_id}",
                    'views': item['monthly_views'],
                    'change': item['views_change']
                }
                for item in top_performers
            ]
        
        # Calculate monthly growth (compare with previous month)
        if report_month == 1:
            prev_month = 12
            prev_year = report_year - 1
        else:
            prev_month = report_month - 1
            prev_year = report_year
        
        prev_month_total = session.execute(
            select(func.sum(MonthlyView.views)).where(
                and_(
                    MonthlyView.user_id == user.id,
                    MonthlyView.year == prev_year,
                    MonthlyView.month == prev_month
                )
            )
        ).scalar() or 0
        
        if prev_month_total > 0:
            report_summary['monthly_growth'] = ((total_views - prev_month_total) / prev_month_total) * 100
        
        bot_logger.info(f"Monthly report generated for {user.discord_username}: {total_views:,} views, {total_likes:,} likes")
        
        # Store report in database or send notification
        store_monthly_report.delay(report_summary)
        
        return report_summary


@celery_app.task
def store_monthly_report(report_summary: Dict):
    """Store monthly report summary in database"""
    # This could store the report in a new table for historical tracking
    # For now, we'll just log it
    bot_logger.info(f"Monthly report stored for user {report_summary['discord_username']}: {report_summary['total_views']:,} views")


@celery_app.task
def sync_new_videos_for_user(user_id: int):
    """Sync new videos from automatic channels for a specific user"""
    with session_scope() as session:
        user = session.execute(
            select(User).where(User.id == user_id)
        ).scalar_one_or_none()
        
        if not user:
            bot_logger.error(f"User {user_id} not found")
            return
        
        # Get user's automatic channels
        channels = session.execute(
            select(Channel).where(
                and_(
                    Channel.user_id == user.id,
                    Channel.is_verified == True,
                    Channel.verification_mode == "automatic",
                    Channel.is_active == True
                )
            )
        ).scalars().all()
        
        if not channels:
            bot_logger.info(f"No automatic channels found for user {user.discord_username}")
            return
        
        synced_count = 0
        error_count = 0
        
        for channel in channels:
            try:
                # Fetch recent videos from channel
                videos = fetch_channel_videos(channel.channel_id, max_results=10)
                
                for video_info in videos:
                    # Check if video already exists
                    existing_video = session.execute(
                        select(Video).where(
                            and_(
                                Video.user_id == user.id,
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
                        user_id=user.id,
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
                        user_id=user.id,
                        video_id=video.id,
                        year=now.year,
                        month=now.month,
                        views=stats.view_count,
                        updated_at=now
                    )
                    session.add(monthly_view)
                    
                    synced_count += 1
                
                # Update last sync time
                channel.last_sync_at = datetime.now(timezone.utc)
                
            except Exception as e:
                bot_logger.error(f"Error syncing channel {channel.channel_id} for user {user.discord_username}: {e}")
                error_count += 1
                continue
        
        session.commit()
        
        if synced_count > 0:
            bot_logger.info(f"Synced {synced_count} new videos for user {user.discord_username}")
        
        return {"synced": synced_count, "errors": error_count}


@celery_app.task
def refresh_user_video_stats(user_id: int):
    """Refresh stats for all videos of a specific user"""
    with session_scope() as session:
        user = session.execute(
            select(User).where(User.id == user_id)
        ).scalar_one_or_none()
        
        if not user:
            bot_logger.error(f"User {user_id} not found")
            return
        
        # Get all user's videos
        videos = session.execute(
            select(Video).where(Video.user_id == user.id)
        ).scalars().all()
        
        if not videos:
            bot_logger.info(f"No videos found for user {user.discord_username}")
            return
        
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
                            MonthlyView.user_id == user.id,
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
                    monthly_view = MonthlyView(
                        user_id=user.id,
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
                bot_logger.error(f"Error updating video {video.video_id} for user {user.discord_username}: {e}")
                error_count += 1
                continue
        
        session.commit()
        
        bot_logger.info(f"Updated {updated_count} videos for user {user.discord_username}")
        return {"updated": updated_count, "errors": error_count}
