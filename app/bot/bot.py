import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import sys
import atexit
import random
import string
from datetime import datetime, timezone
from sqlalchemy import select, and_, func
from app.models import User, Video, MonthlyView, Channel
from app.utils.logger import bot_logger
from app.services.youtube import fetch_channel_videos, fetch_video_stats, is_valid_youtube_url, is_channel_url, parse_channel_id, get_channel_id_from_username_async, fetch_channel_info_async, check_verification
from app.services.quota_manager import quota_manager
from app.tasks.refresh_stats import refresh_video_stats
from app.tasks.automatic_tracking import sync_new_videos_from_channels
from app.config import settings
from app.infrastructure.db import session_scope

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="/", intents=intents)

# Helper: Generate verification code
def generate_verification_code(length: int = 6) -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))

# Helper: Format numbers
def format_number(num: int) -> str:
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
    print(f"Bot is ready as {bot.user}")

@bot.tree.command(name="help", description="Show help for all available commands.")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🤖 DataBot Commands",
        description="Here are all available commands:",
        color=0x00ff00
    )
    embed.add_field(
        name="📋 Core Commands",
        value=(
            "`/help` - Show this help message\n"
            "`/register` - Accept Terms of Service to get clipper role\n"
            "`/verify <url>` - Verify your YouTube channel\n"
            "`/done` - Complete verification after adding code\n"
            "`/add <url>` - Add a video from your verified channel"
        ),
        inline=False
    )
    embed.add_field(
        name="📊 Stats Commands",
        value=(
            "`/stats` - Show live stats & auto-sync videos\n"
            "`/report [month] [year]` - Generate live monthly report\n"
            "`/monthly` - Show monthly summary\n"
            "`/channels` - List your channels\n"
            "`/videos` - List tracked videos"
        ),
        inline=False
    )
    embed.add_field(
        name="⚙️ Management",
        value="`/remove <video_id>` - Remove video from tracking",
        inline=False
    )
    embed.add_field(
        name="💡 Tips",
        value=(
            "• **Start with `/register` to accept Terms of Service and get access**\n"
            "• Use `/verify <url> automatic` for auto-tracking all videos\n"
            "• Use `/verify <url> manual` for manual video tracking\n"
            "• Add the verification code to your channel description\n"
            "• **Wait 5 minutes** before running `/done`\n"
            "• Use `/report [month] [year]` for historical data\n"
            "• Example: `/report 12 2024` for December 2024"
        ),
        inline=False
    )
    embed.set_footer(text="DataBot - Track your YouTube growth!")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="register", description="Accept Terms of Service to get clipper role and access to all commands.")
async def register_command(interaction: discord.Interaction):
    clipper_role = discord.utils.get(interaction.guild.roles, name="clipper")
    if clipper_role and clipper_role in interaction.user.roles:
        embed = discord.Embed(
            title="✅ Already Registered",
            description="You already have the clipper role and can use all commands!",
            color=0x00ff00
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
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
    view = TOSView(interaction.user.id)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@bot.tree.command(name="verify", description="Verify your YouTube channel ownership for tracking.")
async def verify_command(interaction: discord.Interaction, url: str, mode: str = "manual"):
    if not is_valid_youtube_url(url):
        await interaction.response.send_message("Invalid YouTube URL", ephemeral=True)
        return
    if not is_channel_url(url):
        await interaction.response.send_message("Please provide a channel URL, not a video URL", ephemeral=True)
        return
    if mode not in ["manual", "automatic"]:
        await interaction.response.send_message("Mode must be 'manual' or 'automatic'", ephemeral=True)
        return
    processing_embed = discord.Embed(
        title="🔍 Processing Channel",
        description="Fetching channel information...",
        color=0xffff00
    )
    await interaction.response.send_message(embed=processing_embed, ephemeral=True)
    channel_id = parse_channel_id(url)
    if not channel_id:
        await interaction.followup.send("Could not parse channel from URL", ephemeral=True)
        return
    if not channel_id.startswith("UC"):
        channel_id = await get_channel_id_from_username_async(channel_id)
        if not channel_id:
            await interaction.followup.send("Could not find YouTube channel", ephemeral=True)
            return
    channel_info = await fetch_channel_info_async(channel_id)
    if not channel_info:
        await interaction.followup.send("Could not fetch channel information", ephemeral=True)
        return
    with session_scope() as session:
        user = session.execute(
            select(User).where(User.discord_user_id == str(interaction.user.id))
        ).scalar_one_or_none()
        if user is None:
            user = User(
                discord_user_id=str(interaction.user.id),
                discord_username=interaction.user.display_name,
                created_at=datetime.now(timezone.utc)
            )
            session.add(user)
            session.flush()
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
                await interaction.followup.send("Channel already verified!", ephemeral=True)
                return
            else:
                existing_channel.verification_code = generate_verification_code()
                existing_channel.verification_mode = mode
                verification_code = existing_channel.verification_code
        else:
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
            description=f"Add `{verification_code}` to your channel description, then run /done",
            color=0xffff00
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="videos", description="List all your tracked videos.")
async def videos_command(interaction: discord.Interaction):
    with session_scope() as session:
        user = session.execute(
            select(User).where(User.discord_user_id == str(interaction.user.id))
        ).scalar_one_or_none()
        if user is None:
            await interaction.response.send_message("No videos found. Use /add to add your first video", ephemeral=True)
            return
        videos = session.execute(
            select(Video).where(Video.user_id == user.id).order_by(Video.created_at.desc())
        ).scalars().all()
        if not videos:
            embed = discord.Embed(
                title="📺 No Videos",
                description="Use /add to add your first video.",
                color=0x00ff00
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        embed = discord.Embed(
            title="📺 Your Videos",
            description=f"{len(videos)} video(s):",
            color=0x00ff00
        )
        top_videos = sorted(videos, key=lambda x: x.last_view_count, reverse=True)[:5]
        video_text = []
        for i, video in enumerate(top_videos, 1):
            title = video.title or f"Video {video.video_id}"
            video_text.append(f"{i}. {title} - {format_number(video.last_view_count)}")
        embed.add_field(name="Top Videos", value="\n".join(video_text), inline=False)
        if len(videos) > 5:
            embed.add_field(name="More", value=f"+{len(videos) - 5} more videos", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="remove", description="Remove a video from tracking.")
async def remove_command(interaction: discord.Interaction, video_id: str):
    with session_scope() as session:
        user = session.execute(
            select(User).where(User.discord_user_id == str(interaction.user.id))
        ).scalar_one_or_none()
        if user is None:
            await interaction.response.send_message("No videos found", ephemeral=True)
            return
        video = session.execute(
            select(Video).where(
                and_(
                    Video.user_id == user.id,
                    Video.video_id == video_id
                )
            )
        ).scalar_one_or_none()
        if not video:
            await interaction.response.send_message(f"No video found with ID `{video_id}`. Use /videos to see your tracked videos", ephemeral=True)
            return
        title = video.title or f"Video {video.video_id}"
        session.delete(video)
        embed = discord.Embed(
            title="✅ Removed",
            description=f"Removed **{title}** from tracking.",
            color=0x00ff00
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

class TOSView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=300)
        self.user_id = user_id
    @discord.ui.button(label="✅ Accept TOS", style=discord.ButtonStyle.green, emoji="📜")
    async def accept_tos(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ This button is not for you!", ephemeral=True)
            return
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
        try:
            await interaction.user.add_roles(clipper_role)
            embed = discord.Embed(
                title="✅ Terms of Service Accepted",
                description=(
                    "**Welcome to the Filian Clipping Community!** 🎉\n\n"
                    "You have successfully accepted the Terms of Service and received the **clipper** role.\n\n"
                    "**You can now use all DataBot commands:**\n"
                    "• `/verify <channel_url>` - Add your YouTube/TikTok/Instagram channel\n"
                    "• `/add <video_url>` - Track a video (monthly views only)\n"
                    "• `/videos` - View your tracked videos\n"
                    "• `/stats` - View monthly view statistics\n"
                    "• `/help` - See all available commands\n\n"
                    "**Happy clipping!** ✂️"
                ),
                color=0x00ff00
            )
            await interaction.response.send_message(embed=embed, ephemeral=False)
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Bot doesn't have permission to assign roles. Please ask an admin to assign the 'clipper' role manually.",
                ephemeral=True
            )
    @discord.ui.button(label="❌ Decline", style=discord.ButtonStyle.red, emoji="🚫")
    async def decline_tos(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ This button is not for you!", ephemeral=True)
            return
        embed = discord.Embed(
            title="❌ Terms of Service Declined",
            description=(
                "You have declined the Terms of Service.\n\n"
                "**You cannot use DataBot commands without accepting the TOS.**\n\n"
                "If you change your mind, you can run `/register` again to accept the terms."
            ),
            color=0xff0000
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

def main():
    bot.run(settings.discord_bot_token)

if __name__ == "__main__":
    main()


