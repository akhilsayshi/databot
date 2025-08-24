"""
YouTubeBot - Discord bot for tracking YouTube video views.
MVP focused on YouTube-only functionality.
"""

import os
import random
import string
import asyncio
import signal
import sys
import atexit
import warnings
from datetime import datetime, timezone
from typing import Optional
from functools import wraps

# Suppress aiohttp connector warnings
warnings.filterwarnings("ignore", message="Unclosed connector")
warnings.filterwarnings("ignore", message="Unclosed client session")

# Import discord without voice support to avoid audioop dependency
import discord
from discord.ext import commands

# Disable voice support to avoid audioop import
discord.VoiceClient = None

from sqlalchemy import select, and_, func
from sqlalchemy.orm import joinedload
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.infrastructure.db import session_scope
from app.models import User, Channel, Video, MonthlyView
from app.services.youtube import (
    parse_video_id, parse_channel_id, get_channel_id_from_username,
    fetch_video_stats, fetch_channel_info, fetch_channel_videos,
    check_verification, is_valid_youtube_url, is_video_url, is_channel_url,
    fetch_channel_info_async, get_channel_id_from_username_async,
    get_video_channel_id
)
from app.tasks.monthly_reports import refresh_user_video_stats, sync_new_videos_for_user
from app.utils.logger import bot_logger


# Bot configuration
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None
)

# Global flag to track if bot is running
_bot_running = False

def cleanup_bot():
    """Cleanup function to properly close bot connections"""
    global _bot_running
    if _bot_running and not bot.is_closed():
        print("🔄 Cleaning up bot connections...")
        try:
            # Close bot connection gracefully
            loop = None
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            if loop.is_running():
                # If event loop is running, schedule the close
                asyncio.create_task(bot.close())
            else:
                # If event loop is not running, run the close synchronously
                loop.run_until_complete(bot.close())
                
        except Exception as e:
            print(f"Error during cleanup: {e}")
        _bot_running = False
        print("✅ Bot cleanup completed")

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    print(f"🔄 Received signal {signum}, shutting down bot...")
    cleanup_bot()
    sys.exit(0)

# Register cleanup handlers
atexit.register(cleanup_bot)
signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


def error_handler(func):
    """Simple error handler for bot commands"""
    @wraps(func)
    async def wrapper(ctx: commands.Context, *args, **kwargs):
        try:
            return await func(ctx, *args, **kwargs)
        except Exception as e:
            embed = discord.Embed(
                title="❌ Error",
                description=str(e),
                color=0xff0000
            )
            await ctx.send(embed=embed)
            bot_logger.error(f"Command error: {e}")
    
    return wrapper


def require_clipper_role():
    """Decorator to require clipper role for commands"""
    def decorator(func):
        @wraps(func)
        async def wrapper(ctx: commands.Context, *args, **kwargs):
            # Check if user has clipper role
            clipper_role = discord.utils.get(ctx.guild.roles, name="clipper")
            if not clipper_role or clipper_role not in ctx.author.roles:
                embed = discord.Embed(
                    title="🔒 Access Denied",
                    description="You need to register first to use this command!\n\nUse `!register` to read the rules and get access to all commands.",
                    color=0xff6b35
                )
                await ctx.send(embed=embed)
                return
            
            return await func(ctx, *args, **kwargs)
        return wrapper
    return decorator


def generate_verification_code(length: int = 6) -> str:
    """Generate a random verification code"""
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


def format_number(num: int) -> str:
    """Format large numbers with K, M, B suffixes"""
    if num >= 1_000_000_000:
        return f"{num / 1_000_000_000:.1f}B"
    elif num >= 1_000_000:
        return f"{num / 1_000_000:.1f}M"
    elif num >= 1_000:
        return f"{num / 1_000:.1f}K"
    else:
        return str(num)


@bot.event
async def on_ready():
    """Called when the bot is ready"""
    global _bot_running
    
    # Check if this is a duplicate instance
    if _bot_running:
        bot_logger.warning("⚠️ Duplicate bot instance detected! Shutting down this instance...")
        print("⚠️ DUPLICATE BOT DETECTED - Terminating this instance")
        await bot.close()
        return
    
    _bot_running = True
    guilds = ", ".join(g.name for g in bot.guilds)
    bot_logger.info(f"✅ DataBot is ready! Logged in as {bot.user}")
    bot_logger.info(f"🔗 Serving {len(bot.guilds)} guild(s): {guilds}")
    bot_logger.info(f"🎯 Bot instance ID: {id(bot)} - Single instance confirmed")
    
    # Print startup confirmation
    print(f"✅ DataBot connected successfully as {bot.user}")
    print(f"🔗 Connected to {len(bot.guilds)} server(s)")
    print(f"🎯 Instance ID: {id(bot)} - Ready for commands!")
    print("🚫 Any duplicate instances will be automatically terminated")


@bot.event
async def on_disconnect():
    """Called when bot disconnects"""
    global _bot_running
    bot_logger.info("🔌 Bot disconnected from Discord")
    _bot_running = False


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    """Global error handler"""
    if isinstance(error, commands.CommandNotFound):
        embed = discord.Embed(
            title="❌ Command Not Found",
            description=f"Unknown command: `{ctx.message.content.split()[0]}`\n\nUse `!help` to see available commands.",
            color=0xff0000
        )
        await ctx.send(embed=embed)
    elif isinstance(error, commands.MissingRequiredArgument):
        embed = discord.Embed(
            title="❌ Missing Argument",
            description=f"Missing required argument: `{error.param.name}`",
            color=0xff0000
        )
        await ctx.send(embed=embed)
    else:
        embed = discord.Embed(
            title="❌ Command Error",
            description="An error occurred while processing your command.",
            color=0xff0000
        )
        await ctx.send(embed=embed)


@bot.command(name="help")
@error_handler
async def help_command(ctx: commands.Context):
    """Show help for all available commands"""
    embed = discord.Embed(
        title="🤖 DataBot Commands",
        description="Here are all available commands:",
        color=0x00ff00
    )
    
    # Core commands
    embed.add_field(
        name="📋 Core Commands",
        value=(
            "`!help` - Show this help message\n"
            "`!register` - Accept Terms of Service to get clipper role\n"
            "`!verify <url>` - Verify your YouTube channel\n"
            "`!done` - Complete verification after adding code\n"
            "`!add <url>` - Add a video from your verified channel"
        ),
        inline=False
    )
    
    # Stats commands
    embed.add_field(
        name="📊 Stats Commands",
        value=(
            "`!stats` - Show live stats & auto-sync videos\n"
            "`!report [month] [year]` - Generate live monthly report\n"
            "`!monthly` - Show monthly summary\n"
            "`!channels` - List your channels\n"
            "`!videos` - List tracked videos"
        ),
        inline=False
    )
    
    # Management commands
    embed.add_field(
        name="⚙️ Management",
        value=(
            "`!sync` - Sync all videos from verified channels\n"
            "`!remove <video_id>` - Remove video from tracking"
        ),
        inline=False
    )
    
    embed.add_field(
        name="💡 Tips",
        value=(
            "• **Start with `!register` to accept Terms of Service and get access**\n"
            "• Use `!verify <url> automatic` for auto-tracking all videos\n"
            "• Use `!verify <url> manual` for manual video tracking\n"
            "• Add the verification code to your channel description\n"
            "• **Wait 5 minutes** before running `!done`\n"
            "• Use `!sync` to sync all videos from verified channels\n"
            "• Use `!report [month] [year]` for historical data\n"
            "• Example: `!report 12 2024` for December 2024"
        ),
        inline=False
    )
    
    embed.set_footer(text="DataBot - Track your YouTube growth!")
    await ctx.send(embed=embed)


@bot.command(name="register")
@error_handler
async def register_command(ctx: commands.Context):
    """Accept Terms of Service to get clipper role and access to all commands"""
    
    # Check if user already has clipper role
    clipper_role = discord.utils.get(ctx.guild.roles, name="clipper")
    if clipper_role and clipper_role in ctx.author.roles:
        embed = discord.Embed(
            title="✅ Already Registered",
            description="You already have the clipper role and can use all commands!",
            color=0x00ff00
        )
        await ctx.send(embed=embed)
        return
    
    # Create TOS embed
    embed = discord.Embed(
        title="📜 Filian Clipping Community - Terms of Service",
        description=(
            "**Welcome to the Filian Clipping Community!** 🎉\n\n"
            "Before you can use DataBot commands, you must accept our Terms of Service.\n\n"
            "**By accepting, you agree to:**\n"
            "• Be Kind & Respectful, Obey Discord's TOS\n"
            "• **ZERO TOLERANCE** for view botting/artificial growth (permaban)\n"
            "• Only upload appropriate Filian content\n"
            "• No stealing other clippers' edits\n"
            "• Follow 350 videos/month limit\n"
            "• Only track monthly views (not total views)\n"
            "• Views count in 2-month cycles\n\n"
            "**Click the button below to accept and become a clipper!**"
        ),
        color=0x0099ff
    )
    
    embed.add_field(
        name="📋 Full Rules",
        value="After accepting, you'll receive the complete rules and guidelines.",
        inline=False
    )
    
    embed.set_footer(text="DataBot - Terms of Service • You have 5 minutes to respond")
    
    # Create and send the view with buttons
    view = TOSView(ctx.author.id)
    await ctx.send(embed=embed, view=view)


@bot.command(name="verify")
@error_handler
@require_clipper_role()
async def verify_command(ctx: commands.Context, url: str, mode: str = "manual"):
    """Verify your YouTube channel ownership for tracking"""
    
    if not is_valid_youtube_url(url):
        raise ValueError("Invalid YouTube URL")
    
    if not is_channel_url(url):
        raise ValueError("Please provide a channel URL, not a video URL")
    
    if mode not in ["manual", "automatic"]:
        raise ValueError("Mode must be 'manual' or 'automatic'")
    
    # Show processing message
    processing_embed = discord.Embed(
        title="🔍 Processing Channel",
        description="Fetching channel information...",
        color=0xffff00
    )
    await ctx.send(embed=processing_embed)
    
    # Parse channel ID
    channel_id = parse_channel_id(url)
    if not channel_id:
        raise ValueError("Could not parse channel from URL")
    
    # If it's a username/handle, convert to channel ID asynchronously
    if not channel_id.startswith("UC"):
        channel_id = await get_channel_id_from_username_async(channel_id)
        if not channel_id:
            raise ValueError("Could not find YouTube channel")
    
    # Fetch channel info to verify it exists asynchronously
    channel_info = await fetch_channel_info_async(channel_id)
    if not channel_info:
        raise ValueError("Could not fetch channel information")
    
    with session_scope() as session:
        # Get or create user
        user = session.execute(
            select(User).where(User.discord_user_id == str(ctx.author.id))
        ).scalar_one_or_none()
        
        if user is None:
            user = User(
                discord_user_id=str(ctx.author.id),
                discord_username=ctx.author.display_name,
                created_at=datetime.now(timezone.utc)
            )
            session.add(user)
            session.flush()
        
        # Check if channel already exists
        existing_channel = session.execute(
            select(Channel).where(
                and_(
                    Channel.user_id == user.id,
                    Channel.channel_id == channel_id
                )
            )
        ).scalar_one_or_none()
        
        if existing_channel:
            if existing_channel.is_verified:
                raise ValueError("Channel already verified!")
            else:
                # Update existing unverified channel
                existing_channel.verification_code = generate_verification_code()
                existing_channel.verification_mode = mode
                verification_code = existing_channel.verification_code
        else:
            # Create new channel
            verification_code = generate_verification_code()
            channel = Channel(
                user_id=user.id,
                channel_id=channel_id,
                channel_name=channel_info.channel_name,
                url=url,
                verification_code=verification_code,
                verification_mode=mode,
                created_at=datetime.now(timezone.utc),
            )
            session.add(channel)
        
        embed = discord.Embed(
            title="🔐 Verification Required",
            description=f"Add `{verification_code}` to your channel description, then run `!done`",
            color=0xffff00
        )
        
        embed.add_field(
            name="Mode",
            value=f"{mode.title()} - {'Manual: Add videos with `!add`' if mode == 'manual' else 'Auto: Track all videos'}",
            inline=False
        )
        
        embed.add_field(
            name="⏰ Important",
            value="**Wait 5 minutes** after adding the code to your description before running `!done`. The verification code will expire after 5 minutes.",
            inline=False
        )
        
        embed.add_field(
            name="📝 Steps",
            value=(
                "1. Copy the verification code above\n"
                "2. Add it to your YouTube channel description\n"
                "3. Save your channel description\n"
                "4. **Wait 5 minutes** for YouTube to update\n"
                "5. Run `!done` to complete verification"
            ),
            inline=False
        )
        
        await ctx.send(embed=embed)


@bot.command(name="done")
@error_handler
@require_clipper_role()
async def done_command(ctx: commands.Context):
    """Complete verification after adding code to channel description"""
    
    with session_scope() as session:
        user = session.execute(
            select(User).where(User.discord_user_id == str(ctx.author.id))
        ).scalar_one_or_none()
        
        if user is None:
            raise ValueError("No verification in progress. Use `!verify` first")
        
        # Get the most recent unverified channel
        channel = session.execute(
            select(Channel).where(
                and_(
                    Channel.user_id == user.id,
                    Channel.is_verified == False
                )
            ).order_by(Channel.created_at.desc())
        ).scalar_one_or_none()
        
        if channel is None:
            raise ValueError("No verification in progress. Use `!verify` first")
        
        # Show checking message
        checking_embed = discord.Embed(
            title="🔍 Checking Verification",
            description=f"Checking for code `{channel.verification_code}` in **{channel.channel_name}**...",
            color=0xffff00
        )
        await ctx.send(embed=checking_embed)
        
        # Check verification with fresh data
        is_verified = check_verification(channel.channel_id, channel.verification_code)
        
        if not is_verified:
            embed = discord.Embed(
                title="⏳ Not Found",
                description=(
                    f"Code `{channel.verification_code}` not found in **{channel.channel_name}**.\n\n"
                    "**Make sure you:**\n"
                    "• Added the code exactly as shown\n"
                    "• Saved your channel description\n"
                    "• Waited a few minutes for YouTube to update\n\n"
                    "Try again in a few minutes or check your channel description."
                ),
                color=0xffff00
            )
            await ctx.send(embed=embed)
            return
        
        # Mark as verified
        channel.is_verified = True
        channel.last_sync_at = datetime.now(timezone.utc)
        
        # If automatic mode, sync videos immediately
        synced_videos = 0
        if channel.verification_mode == "automatic":
            try:
                # Fetch videos from channel
                channel_videos = fetch_channel_videos(channel.channel_id, max_results=50)
                if channel_videos:
                    for video_data in channel_videos:
                        try:
                            # Check if video already exists
                            existing_video = session.execute(
                                select(Video).where(
                                    and_(
                                        Video.user_id == user.id,
                                        Video.video_id == video_data.video_id
                                    )
                                )
                            ).scalar_one_or_none()
                            
                            if not existing_video:
                                # Create new video
                                video = Video(
                                    user_id=user.id,
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
                                synced_videos += 1
                        except Exception as e:
                            bot_logger.error(f"Error adding video {video_data.video_id}: {e}")
                            continue
            except Exception as e:
                bot_logger.error(f"Error syncing videos for channel {channel.channel_id}: {e}")
        
        embed = discord.Embed(
            title="✅ Verified!",
            description=f"**{channel.channel_name}** is now verified!",
            color=0x00ff00
        )
        
        mode_text = "Manual: Use `!add`" if channel.verification_mode == "manual" else f"Auto: {synced_videos} videos synced"
        embed.add_field(name="Mode", value=mode_text, inline=False)
        
        if channel.verification_mode == "automatic" and synced_videos > 0:
            embed.add_field(
                name="🔄 Auto Sync",
                value=f"Automatically synced {synced_videos} videos from your channel!",
                inline=False
            )
        
        embed.add_field(
            name="Next Steps",
            value=(
                "• Use `!add <video_url>` to track videos\n"
                "• Use `!sync` to sync all videos from this channel\n"
                "• Use `!stats` to check your progress\n"
                "• Use `!help` for more commands"
            ),
            inline=False
        )
        
        await ctx.send(embed=embed)


@bot.command(name="add")
@error_handler
@require_clipper_role()
async def add_command(ctx: commands.Context, video_url: str):
    """Add a YouTube video to track views manually"""
    
    if not is_valid_youtube_url(video_url):
        raise ValueError("Invalid YouTube URL")
    
    if not is_video_url(video_url):
        raise ValueError("Please provide a video URL, not a channel URL")
    
    # Parse video ID
    video_id = parse_video_id(video_url)
    if not video_id:
        raise ValueError("Could not parse video from URL")
    
    # Get the channel ID for this video
    video_channel_id = get_video_channel_id(video_id)
    if not video_channel_id:
        raise ValueError("Could not determine the channel for this video")
    
    # Fetch video stats
    stats = fetch_video_stats(video_id)
    if not stats:
        raise ValueError("Could not fetch video information")
    
    with session_scope() as session:
        # Get or create user
        user = session.execute(
            select(User).where(User.discord_user_id == str(ctx.author.id))
        ).scalar_one_or_none()
        
        if user is None:
            user = User(
                discord_user_id=str(ctx.author.id),
                discord_username=ctx.author.display_name,
                created_at=datetime.now(timezone.utc)
            )
            session.add(user)
            session.flush()
        
        # Check if video already exists
        existing_video = session.execute(
            select(Video).where(
                and_(
                    Video.user_id == user.id,
                    Video.video_id == video_id
                )
            )
        ).scalar_one_or_none()
        
        if existing_video:
            raise ValueError(f"Video already tracked: {existing_video.title or 'Unknown'}")
        
        # Check if user has verified the channel that this video belongs to
        verified_channel = session.execute(
            select(Channel).where(
                and_(
                    Channel.user_id == user.id,
                    Channel.channel_id == video_channel_id,
                    Channel.is_verified == True
                )
            )
        ).scalar_one_or_none()
        
        if not verified_channel:
            # Get channel info to show user which channel they need to verify
            channel_info = fetch_channel_info(video_channel_id)
            channel_name = channel_info.channel_name if channel_info else "Unknown Channel"
            
            embed = discord.Embed(
                title="❌ Channel Not Verified",
                description=f"You can only add videos from channels you have verified.",
                color=0xff0000
            )
            
            embed.add_field(
                name="Required Action",
                value=f"First verify the channel **{channel_name}** using:\n`!verify https://www.youtube.com/channel/{video_channel_id}`",
                inline=False
            )
            
            embed.add_field(
                name="Video Info",
                value=f"**{stats.title or 'Unknown'}**\nChannel: {channel_name}",
                inline=False
            )
            
            await ctx.send(embed=embed)
            return
        
        # Create video record
        video = Video(
            user_id=user.id,
            channel_id=verified_channel.id,
            video_id=video_id,
            url=video_url,
            title=stats.title,
            description=stats.description,
            thumbnail_url=stats.thumbnail_url,
            published_at=stats.published_at,
            last_view_count=stats.view_count,
            created_at=datetime.now(timezone.utc),
        )
        session.add(video)
        session.flush()
        
        # Create initial monthly view record (start at 0 - only track incremental views from this point)
        now = datetime.now(timezone.utc)
        monthly_view = MonthlyView(
            user_id=user.id,
            video_id=video.id,
            year=now.year,
            month=now.month,
            views=0,  # Start at 0 - only track views gained after adding to bot
            updated_at=now
        )
        session.add(monthly_view)
        
        embed = discord.Embed(
            title="✅ Video Added to Monthly Tracking!",
            description=f"**{stats.title or 'Unknown'}**\n\n🔄 **Now tracking monthly views only**\nCurrent total views: {format_number(stats.view_count)} (not tracked)\nMonthly tracking starts: NOW",
            color=0x00ff00
        )
        
        embed.add_field(
            name="Channel",
            value=f"✅ {verified_channel.channel_name}",
            inline=False
        )
        
        await ctx.send(embed=embed)


@bot.command(name="sync")
@error_handler
@require_clipper_role()
async def sync_command(ctx: commands.Context):
    """Sync videos from automatic channels"""
    
    with session_scope() as session:
        user = session.execute(
            select(User).where(User.discord_user_id == str(ctx.author.id))
        ).scalar_one_or_none()
        
        if user is None:
            raise ValueError("No channels found. Use `!verify` to add your first channel")
        
        # Get automatic channels
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
            embed = discord.Embed(
                title="📺 No Auto Channels",
                description="Use `!verify [url] automatic` to set up automatic tracking.",
                color=0xffff00
            )
            await ctx.send(embed=embed)
            return
        
        total_added = 0
        total_errors = 0
        
        for channel in channels:
            try:
                # Fetch recent videos from channel
                videos = fetch_channel_videos(channel.channel_id, max_results=20)
                
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
                    
                    total_added += 1
                
                # Update last sync time
                channel.last_sync_at = datetime.now(timezone.utc)
                
            except Exception as e:
                total_errors += 1
                bot_logger.error(f"Error syncing channel {channel.channel_id}: {e}")
                continue
        
        embed = discord.Embed(
            title="🔄 Sync Complete",
            description=f"Added {total_added} videos, {total_errors} errors",
            color=0x00ff00
        )
        
        if total_added > 0:
            embed.add_field(
                name="🎯 Next Steps",
                value="Use `!stats` to see your updated stats.",
                inline=False
            )
        
        await ctx.send(embed=embed)


@bot.command(name="stats")
@error_handler
@require_clipper_role()
async def stats_command(ctx: commands.Context):
    """Show your current stats for tracked videos with automatic syncing"""
    
    # Show processing message
    processing_embed = discord.Embed(
        title="🔄 Fetching Live Stats & Syncing Videos",
        description="Getting updated view counts and syncing new videos...",
        color=0xffff00
    )
    processing_msg = await ctx.send(embed=processing_embed)
    
    with session_scope() as session:
        user = session.execute(
            select(User).where(User.discord_user_id == str(ctx.author.id))
        ).scalar_one_or_none()
        
        if user is None:
            raise ValueError("No videos tracked yet. Use `!add` to add your first video")
        
        # Trigger background tasks for automatic syncing
        try:
            # Sync new videos from automatic channels
            sync_task = sync_new_videos_for_user.delay(user.id)
            
            # Refresh stats for all videos
            refresh_task = refresh_user_video_stats.delay(user.id)
            
            bot_logger.info(f"Triggered background tasks for user {user.discord_username}: sync={sync_task.id}, refresh={refresh_task.id}")
        except Exception as e:
            bot_logger.error(f"Error triggering background tasks for user {user.discord_username}: {e}")
        
        # Get all user's videos (including newly synced ones)
        videos = session.execute(
            select(Video).where(Video.user_id == user.id)
        ).scalars().all()
        
        if not videos:
            embed = discord.Embed(
                title="📊 No Videos",
                description="No videos tracked yet. Use `!add` to add your first video.",
                color=0x00ff00
            )
            await processing_msg.edit(embed=embed)
            return
        
        # Fetch real-time stats for all videos
        updated_videos = []
        monthly_views_total = 0
        total_likes = 0
        update_errors = 0
        
        for video in videos:
            try:
                # Fetch current stats from YouTube API
                current_stats = fetch_video_stats(video.video_id)
                if current_stats:
                    # Calculate view change
                    view_change = current_stats.view_count - video.last_view_count
                    
                    # Update video record with current stats
                    video.last_view_count = current_stats.view_count
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
                        # Update existing monthly record with ONLY incremental views
                        if view_change > 0:
                            monthly_view.views += view_change  # Add only new views to monthly total
                        monthly_view.updated_at = now
                    else:
                        # Create new monthly record starting from 0
                        monthly_view = MonthlyView(
                            user_id=user.id,
                            video_id=video.id,
                            year=now.year,
                            month=now.month,
                            views=max(0, view_change),  # Only track new views gained this month
                            updated_at=now
                        )
                        session.add(monthly_view)
                    
                    # Add to totals
                    monthly_views_total += monthly_view.views
                    total_likes += current_stats.like_count
                    
                    # Store for display
                    updated_videos.append({
                        'video': video,
                        'stats': current_stats,
                        'view_change': view_change
                    })
                    
                else:
                    update_errors += 1
                    
            except Exception as e:
                update_errors += 1
                bot_logger.error(f"Error updating stats for video {video.video_id}: {e}")
                continue
        
        # Commit all updates
        session.commit()
        
        # Create stats embed
        now = datetime.now(timezone.utc)
        embed = discord.Embed(
            title="📊 Monthly Views Updated",
            description=f"**{now.strftime('%B %Y')}** - {format_number(monthly_views_total)} monthly views ({len(updated_videos)} videos)",
            color=0x00ff00
        )
        
        # Add summary stats
        embed.add_field(
            name="📈 Summary",
            value=f"Monthly Views: {format_number(monthly_views_total)}\nTotal Likes: {format_number(total_likes)}\nVideos: {len(updated_videos)}",
            inline=True
        )
        
        if update_errors > 0:
            embed.add_field(
                name="⚠️ Errors",
                value=f"{update_errors} videos couldn't be updated",
                inline=True
            )
        
        # Add automatic syncing info
        embed.add_field(
            name="🔄 Auto Sync",
            value="Background tasks triggered to sync new videos and refresh stats",
            inline=True
        )
        
        # Add top performing videos
        if updated_videos:
            # Sort by monthly views instead of total views
            def get_monthly_views(video_data):
                video = video_data['video']
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
                return monthly_view.views if monthly_view else 0
            
            top_videos = sorted(updated_videos, key=get_monthly_views, reverse=True)[:5]
            
            top_videos_text = []
            for i, video_data in enumerate(top_videos, 1):
                video = video_data['video']
                stats = video_data['stats']
                change = video_data['view_change']
                
                title = video.title or f"Video {video.video_id}"
                change_text = f" (+{format_number(change)})" if change > 0 else f" ({format_number(change)})" if change < 0 else ""
                
                # Get monthly views for this video
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
                
                monthly_views = monthly_view.views if monthly_view else 0
                top_videos_text.append(f"{i}. {title} - {format_number(monthly_views)} monthly views")
            
            embed.add_field(
                name="🏆 Top Videos",
                value="\n".join(top_videos_text),
                inline=False
            )
        
        # Add recent changes
        recent_changes = [v for v in updated_videos if v['view_change'] != 0]
        if recent_changes:
            # Sort by absolute change
            recent_changes.sort(key=lambda x: abs(x['view_change']), reverse=True)
            
            changes_text = []
            for video_data in recent_changes[:3]:
                video = video_data['video']
                change = video_data['view_change']
                
                title = video.title or f"Video {video.video_id}"
                change_text = f"+{format_number(change)}" if change > 0 else f"{format_number(change)}"
                
                changes_text.append(f"• {title}: {change_text}")
            
            embed.add_field(
                name="📊 Recent Changes",
                value="\n".join(changes_text),
                inline=False
            )
        
        embed.set_footer(text=f"Last updated: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC | Auto-sync enabled")
        
        await processing_msg.edit(embed=embed)


@bot.command(name="monthly")
@error_handler
@require_clipper_role()
async def monthly_command(ctx: commands.Context):
    """Show monthly summary with automatic tracking info"""
    
    with session_scope() as session:
        user = session.execute(
            select(User).where(User.discord_user_id == str(ctx.author.id))
        ).scalar_one_or_none()
        
        if user is None:
            raise ValueError("No videos tracked yet. Use `!add` to add your first video")
        
        # Get current month's data
        now = datetime.now(timezone.utc)
        monthly_views = session.execute(
            select(MonthlyView).where(
                and_(
                    MonthlyView.user_id == user.id,
                    MonthlyView.year == now.year,
                    MonthlyView.month == now.month
                )
            ).options(joinedload(MonthlyView.video))
        ).scalars().all()
        
        if not monthly_views:
            embed = discord.Embed(
                title="📊 No Monthly Data",
                description="No data for this month yet. Use `!stats` to start tracking.",
                color=0x00ff00
            )
            await ctx.send(embed=embed)
            return
        
        # Calculate totals
        monthly_views_total = sum(mv.views for mv in monthly_views)
        total_videos = len(monthly_views)
        total_changes = sum(mv.views_change for mv in monthly_views)
        
        embed = discord.Embed(
            title="📊 Monthly Summary",
            description=f"**{now.strftime('%B %Y')}** - {format_number(monthly_views_total)} views ({total_videos} videos)",
            color=0x00ff00
        )
        
        # Add summary stats
        embed.add_field(
            name="📈 This Month",
            value=f"Monthly Views: {format_number(monthly_views_total)}\nVideos: {total_videos}\nGrowth: {format_number(total_changes)}",
            inline=True
        )
        
        # Check automatic channels
        automatic_channels = session.execute(
            select(Channel).where(
                and_(
                    Channel.user_id == user.id,
                    Channel.is_verified == True,
                    Channel.verification_mode == "automatic",
                    Channel.is_active == True
                )
            )
        ).scalars().all()
        
        if automatic_channels:
            embed.add_field(
                name="🔄 Auto Tracking",
                value=f"**{len(automatic_channels)}** automatic channels\nNew videos auto-synced",
                inline=True
            )
        else:
            embed.add_field(
                name="🔄 Auto Tracking",
                value="No automatic channels\nUse `!verify [url] automatic`",
                inline=True
            )
        
        # Add top videos
        top_videos = sorted(monthly_views, key=lambda x: x.views, reverse=True)[:5]
        if top_videos:
            video_text = []
            for i, mv in enumerate(top_videos, 1):
                title = mv.video.title or f"Video {mv.video.video_id}"
                change_text = f" (+{format_number(mv.views_change)})" if mv.views_change > 0 else f" ({format_number(mv.views_change)})" if mv.views_change < 0 else ""
                video_text.append(f"{i}. {title} - {format_number(mv.views)}{change_text}")
            
            embed.add_field(
                name="🏆 Top Videos This Month",
                value="\n".join(video_text),
                inline=False
            )
        
        # Add end-of-month info
        embed.add_field(
            name="📅 End of Month",
            value="Automatic comprehensive report will be generated at month end",
            inline=False
        )
        
        embed.set_footer(text=f"Run !stats to sync new videos | Auto-tracking enabled")
        
        await ctx.send(embed=embed)


@bot.command(name="report")
@error_handler
@require_clipper_role()
async def report_command(ctx: commands.Context, month: Optional[int] = None, year: Optional[int] = None):
    """Show monthly aggregated view report with real-time updates"""
    
    now = datetime.now(timezone.utc)
    month = month or now.month
    year = year or now.year
    
    # Validate month and year
    if not (1 <= month <= 12):
        raise ValueError("Month must be between 1 and 12")
    if not (2020 <= year <= 2030):
        raise ValueError("Year must be between 2020 and 2030")
    
    # Check if requesting future date
    if year > now.year or (year == now.year and month > now.month):
        raise ValueError("Cannot generate report for future months")
    
    # Show processing message
    processing_embed = discord.Embed(
        title="🔄 Fetching Live Report",
        description=f"Getting updated stats for {datetime(year, month, 1).strftime('%B %Y')}...",
        color=0xffff00
    )
    processing_msg = await ctx.send(embed=processing_embed)
    
    with session_scope() as session:
        user = session.execute(
            select(User).where(User.discord_user_id == str(ctx.author.id))
        ).scalar_one_or_none()
        
        if user is None:
            raise ValueError("No data found. Use `!add` to add videos first")
        
        # Get all user's videos
        videos = session.execute(
            select(Video).where(Video.user_id == user.id)
        ).scalars().all()
        
        if not videos:
            embed = discord.Embed(
                title="📊 No Videos",
                description="No videos tracked yet. Use `!add` to add your first video.",
                color=0x00ff00
            )
            await processing_msg.edit(embed=embed)
            return
        
        # Fetch real-time stats for all videos
        updated_videos = []
        monthly_views_total = 0
        total_likes = 0
        update_errors = 0
        
        for video in videos:
            try:
                # Fetch current stats from YouTube API
                current_stats = fetch_video_stats(video.video_id)
                if current_stats:
                    # Calculate view change
                    view_change = current_stats.view_count - video.last_view_count
                    
                    # Update video record with current stats
                    video.last_view_count = current_stats.view_count
                    video.last_updated_at = datetime.now(timezone.utc)
                    
                    # Update or create monthly view record
                    monthly_view = session.execute(
                        select(MonthlyView).where(
                            and_(
                                MonthlyView.user_id == user.id,
                                MonthlyView.video_id == video.id,
                                MonthlyView.year == year,
                                MonthlyView.month == month
                            )
                        )
                    ).scalar_one_or_none()
                    
                    if monthly_view:
                        # Update existing monthly record
                        monthly_view.views = current_stats.view_count
                        monthly_view.views_change = view_change
                        monthly_view.updated_at = now
                    else:
                        # Create new monthly record
                        monthly_view = MonthlyView(
                            user_id=user.id,
                            video_id=video.id,
                            year=year,
                            month=month,
                            views=current_stats.view_count,
                            views_change=view_change,
                            updated_at=now
                        )
                        session.add(monthly_view)
                    
                    # Add to totals
                    monthly_views_total += monthly_view.views
                    total_likes += current_stats.like_count
                    
                    # Store for display
                    updated_videos.append({
                        'video': video,
                        'stats': current_stats,
                        'view_change': view_change
                    })
                    
                else:
                    update_errors += 1
                    
            except Exception as e:
                update_errors += 1
                bot_logger.error(f"Error updating stats for video {video.video_id}: {e}")
                continue
        
        # Commit all updates
        session.commit()
        
        # Get historical data for comparison
        previous_month = month - 1 if month > 1 else 12
        previous_year = year if month > 1 else year - 1
        
        # Get previous month's total views
        previous_month_views = session.execute(
            select(func.sum(MonthlyView.views)).where(
                and_(
                    MonthlyView.user_id == user.id,
                    MonthlyView.year == previous_year,
                    MonthlyView.month == previous_month
                )
            )
        ).scalar() or 0
        
        # Calculate month-over-month growth
        growth = monthly_views_total - previous_month_views
        growth_percentage = (growth / previous_month_views * 100) if previous_month_views > 0 else 0
        
        # Create report embed
        embed = discord.Embed(
            title="📊 Live Monthly Report",
            description=f"**{datetime(year, month, 1).strftime('%B %Y')}**",
            color=0x00ff00
        )
        
        # Summary with growth indicators
        growth_emoji = "📈" if growth >= 0 else "📉"
        growth_text = f"{growth_emoji} {format_number(growth)} ({growth_percentage:+.1f}%)"
        
        embed.add_field(
            name="📈 Summary",
            value=f"Monthly Views: {format_number(monthly_views_total)}\nTotal Likes: {format_number(total_likes)}\nVideos: {len(updated_videos)}\nGrowth: {growth_text}",
            inline=True
        )
        
        # Progress tracking
        if year == now.year and month == now.month:
            # Current month - show progress
            days_in_month = (datetime(year, month + 1, 1) - datetime(year, month, 1)).days if month < 12 else 31
            days_elapsed = now.day
            progress_percentage = (days_elapsed / days_in_month) * 100
            
            embed.add_field(
                name="📅 Monthly Progress",
                value=f"Day {days_elapsed}/{days_in_month}\nProgress: {progress_percentage:.1f}%\nAvg daily: {format_number(monthly_views_total // days_elapsed) if days_elapsed > 0 else 0}",
                inline=True
            )
        else:
            # Historical month - show comparison
            embed.add_field(
                name="📊 Historical Data",
                value=f"Previous month: {format_number(previous_month_views)}\nGrowth: {growth_text}\nPeriod: {datetime(year, month, 1).strftime('%B %Y')}",
                inline=True
            )
        
        if update_errors > 0:
            embed.add_field(
                name="⚠️ Errors",
                value=f"{update_errors} videos couldn't be updated",
                inline=True
            )
        
        # Add top videos with better formatting
        if updated_videos:
            # Sort by current view count
            top_videos = sorted(updated_videos, key=lambda x: x['stats'].view_count, reverse=True)[:5]
            
            video_details = []
            for i, video_data in enumerate(top_videos, 1):
                video = video_data['video']
                stats = video_data['stats']
                change = video_data['view_change']
                
                title = video.title or f"Video {video.video_id}"
                if len(title) > 40:
                    title = title[:37] + "..."
                
                change_emoji = "🟢" if change > 0 else "🔴" if change < 0 else "⚪"
                change_text = f" {change_emoji} {format_number(change)}" if change != 0 else ""
                
                video_details.append(f"**{i}.** {title}\n   {format_number(stats.view_count)} views{change_text}")
            
            embed.add_field(name="🏆 Top Videos", value="\n".join(video_details), inline=False)
        
        # Add footer with more info
        footer_text = f"DataBot Report | {now.strftime('%Y-%m-%d %H:%M:%S')} UTC"
        if year == now.year and month == now.month:
            footer_text += " | Live Data"
        else:
            footer_text += " | Historical Data"
        
        embed.set_footer(text=footer_text)
        
        await processing_msg.edit(embed=embed)


@bot.command(name="channels")
@error_handler
@require_clipper_role()
async def channels_command(ctx: commands.Context):
    """List your verified channels"""
    
    with session_scope() as session:
        user = session.execute(
            select(User).where(User.discord_user_id == str(ctx.author.id))
        ).scalar_one_or_none()
        
        if user is None:
            raise ValueError("No channels found. Use `!verify` to add your first channel")
        
        channels = session.execute(
            select(Channel).where(Channel.user_id == user.id).order_by(Channel.created_at.desc())
        ).scalars().all()
        
        if not channels:
            embed = discord.Embed(
                title="📺 No Channels",
                description="Use `!verify` to add your first channel.",
                color=0x00ff00
            )
            await ctx.send(embed=embed)
            return
        
        embed = discord.Embed(
            title="📺 Your Channels",
            description=f"{len(channels)} channel(s):",
            color=0x00ff00
        )
        
        for channel in channels:
            status = "✅" if channel.is_verified else "⏳"
            mode = "Auto" if channel.verification_mode == "automatic" else "Manual"
            
            embed.add_field(
                name=f"{channel.channel_name}",
                value=f"{status} {mode}",
                inline=True
            )
        
        await ctx.send(embed=embed)


@bot.command(name="videos")
@error_handler
@require_clipper_role()
async def videos_command(ctx: commands.Context):
    """List all your tracked videos"""
    
    with session_scope() as session:
        user = session.execute(
            select(User).where(User.discord_user_id == str(ctx.author.id))
        ).scalar_one_or_none()
        
        if user is None:
            raise ValueError("No videos found. Use `!add` to add your first video")
        
        videos = session.execute(
            select(Video).where(Video.user_id == user.id).order_by(Video.created_at.desc())
        ).scalars().all()
        
        if not videos:
            embed = discord.Embed(
                title="📺 No Videos",
                description="Use `!add` to add your first video.",
                color=0x00ff00
            )
            await ctx.send(embed=embed)
            return
        
        embed = discord.Embed(
            title="📺 Your Videos",
            description=f"{len(videos)} video(s):",
            color=0x00ff00
        )
        
        # Show top videos
        top_videos = sorted(videos, key=lambda x: x.last_view_count, reverse=True)[:5]
        video_text = []
        for i, video in enumerate(top_videos, 1):
            title = video.title or f"Video {video.video_id}"
            video_text.append(f"{i}. {title} - {format_number(video.last_view_count)}")
        
        embed.add_field(name="Top Videos", value="\n".join(video_text), inline=False)
        
        if len(videos) > 5:
            embed.add_field(name="More", value=f"+{len(videos) - 5} more videos", inline=False)
        
        await ctx.send(embed=embed)





@bot.command(name="remove")
@error_handler
@require_clipper_role()
async def remove_command(ctx: commands.Context, video_id: str):
    """Remove a video from tracking"""
    
    with session_scope() as session:
        user = session.execute(
            select(User).where(User.discord_user_id == str(ctx.author.id))
        ).scalar_one_or_none()
        
        if user is None:
            raise ValueError("No videos found")
        
        # Find video by video_id
        video = session.execute(
            select(Video).where(
                and_(
                    Video.user_id == user.id,
                    Video.video_id == video_id
                )
            )
        ).scalar_one_or_none()
        
        if not video:
            raise ValueError(f"No video found with ID `{video_id}`. Use `!videos` to see your tracked videos")
        
        title = video.title or f"Video {video.video_id}"
        
        # Delete the video (cascade will handle monthly_views)
        session.delete(video)
        
        embed = discord.Embed(
            title="✅ Removed",
            description=f"Removed **{title}** from tracking.",
            color=0x00ff00
        )
        
        await ctx.send(embed=embed)


class TOSView(discord.ui.View):
    """View for Terms of Service acceptance"""
    
    def __init__(self, user_id: int):
        super().__init__(timeout=300)  # 5 minute timeout
        self.user_id = user_id
    
    @discord.ui.button(label="✅ Accept TOS", style=discord.ButtonStyle.green, emoji="📜")
    async def accept_tos(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handle TOS acceptance"""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ This button is not for you!", ephemeral=True)
            return
        
        # Get or create clipper role
        clipper_role = discord.utils.get(interaction.guild.roles, name="clipper")
        if not clipper_role:
            try:
                clipper_role = await interaction.guild.create_role(
                    name="clipper",
                    color=discord.Color.blue(),
                    reason="DataBot TOS acceptance role"
                )
            except discord.Forbidden:
                await interaction.response.send_message(
                    "❌ Bot doesn't have permission to create roles. Please ask an admin to create a 'clipper' role.",
                    ephemeral=True
                )
                return
        
        # Assign role to user
        try:
            await interaction.user.add_roles(clipper_role)
            
            embed = discord.Embed(
                title="✅ Terms of Service Accepted",
                description=(
                    "**Welcome to the Filian Clipping Community!** 🎉\n\n"
                    "You have successfully accepted the Terms of Service and received the **clipper** role.\n\n"
                    "**You can now use all DataBot commands:**\n"
                    "• `!verify <channel_url>` - Add your YouTube/TikTok/Instagram channel\n"
                    "• `!add <video_url>` - Track a video (monthly views only)\n"
                    "• `!videos` - View your tracked videos\n"
                    "• `!stats` - View monthly view statistics\n"
                    "• `!help` - See all available commands\n\n"
                    "**Happy clipping!** ✂️"
                ),
                color=0x00ff00
            )
            
            # Add Filian-specific rules after acceptance
            embed.add_field(
                 name="📜 Filian Clipping Rules & Guidelines",
                 value=(
                     "**🎯 Core Rules:**\n"
                     "• Be Kind & Respectful, Obey Discord's TOS\n"
                     "• **ZERO TOLERANCE** for view botting/artificial growth → permaban\n"
                     "• You keep ALL money your channels earn + get paid for views!\n\n"
                     "**📹 Content Guidelines:**\n"
                     "• ✅ Use raw unedited clips from Filian's stream\n"
                     "• ✅ Make your own edits and versions\n"
                     "• ❌ NO downloading/uploading other clippers' edits\n"
                     "• ❌ NO adding gameplay over others' edits\n"
                     "• ❌ NO re-uploading Filian's official shorts\n\n"
                     "**🚀 Upload Rules:**\n"
                     "• Post on ALL platforms (TT, IG, YT) - all views count!\n"
                     "• Link unlimited channels to your Discord\n"
                     "• 350 videos per month maximum\n"
                     "• Only add videos posted in SAME MONTH\n"
                     "• Views count in 2-month cycles"
                 ),
                 inline=False
             )
            
            await interaction.response.send_message(embed=embed, ephemeral=False)
            
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Bot doesn't have permission to assign roles. Please ask an admin to assign the 'clipper' role manually.",
                ephemeral=True
            )
    
    @discord.ui.button(label="❌ Decline", style=discord.ButtonStyle.red, emoji="🚫")
    async def decline_tos(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Handle TOS decline"""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ This button is not for you!", ephemeral=True)
            return
        
        embed = discord.Embed(
            title="❌ Terms of Service Declined",
            description=(
                "You have declined the Terms of Service.\n\n"
                "**You cannot use DataBot commands without accepting the TOS.**\n\n"
                "If you change your mind, you can run `!register` again to accept the terms."
            ),
            color=0xff0000
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)





def main() -> None:
    """Main function to run the bot"""
    global _bot_running
    
    # Check if bot is already running in this process
    if _bot_running:
        print("⚠️ Bot already running in this process - exiting to prevent duplicates")
        return
    
    # Validate configuration first
    try:
        settings.validate()
    except SystemExit:
        # Configuration validation already printed error and exited
        return
    
    # Additional token validation
    if not settings.discord_bot_token.startswith("MT"):
        raise ValueError("Invalid Discord bot token format. Token should start with 'MT'")
    
    if len(settings.discord_bot_token) < 50:
        raise ValueError("Discord bot token appears to be too short. Please check your token.")
    
    bot_logger.info("🚀 Starting DataBot (Single Instance Mode)...")
    print("🚀 Starting DataBot with duplicate instance protection...")
    print("✅ Ready to start fresh bot instance")
    
    try:
        # Ensure clean shutdown on exit
        def on_exit():
            cleanup_bot()
        
        atexit.register(on_exit)
        
        # Run the bot
        bot.run(settings.discord_bot_token)
        
    except discord.errors.LoginFailure as e:
        bot_logger.error(f"Discord login failed: {e}")
        print("❌ Discord login failed!")
        print("🔧 This usually means:")
        print("   1. Your DISCORD_BOT_TOKEN is invalid or expired")
        print("   2. The token is not set correctly in Render dashboard")
        print("   3. The bot application was deleted or reset")
        print("\n💡 How to fix:")
        print("   1. Go to Discord Developer Portal")
        print("   2. Reset your bot token")
        print("   3. Update DISCORD_BOT_TOKEN in Render dashboard")
        print("   4. Redeploy the service")
        raise
    except KeyboardInterrupt:
        print("\n🔄 Received shutdown signal...")
        cleanup_bot()
        print("👋 Bot shutdown completed")
    except Exception as e:
        bot_logger.error(f"Failed to start bot: {e}")
        cleanup_bot()
        raise
    finally:
        _bot_running = False


if __name__ == "__main__":
    main()


