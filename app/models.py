from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    Index,
    Boolean,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    discord_user_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    discord_username: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    paypal_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    cashapp_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    channels: Mapped[list[Channel]] = relationship("Channel", back_populates="user", cascade="all, delete-orphan")
    videos: Mapped[list[Video]] = relationship("Video", back_populates="user", cascade="all, delete-orphan")
    monthly_views: Mapped[list[MonthlyView]] = relationship("MonthlyView", back_populates="user", cascade="all, delete-orphan")


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[str] = mapped_column(String(128), index=True)  # YouTube channel ID
    channel_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    url: Mapped[str] = mapped_column(String(512))
    verification_code: Mapped[str] = mapped_column(String(16))
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verification_mode: Mapped[str] = mapped_column(String(16), default="manual")  # manual|automatic
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    user: Mapped[User] = relationship("User", back_populates="channels")
    videos: Mapped[list[Video]] = relationship("Video", back_populates="channel", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("channel_id", name="uq_channel_id"),
        Index("ix_user_channel", "user_id", "channel_id"),
    )


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[Optional[int]] = mapped_column(ForeignKey("channels.id", ondelete="SET NULL"), nullable=True)
    video_id: Mapped[str] = mapped_column(String(128), index=True)  # YouTube video ID
    url: Mapped[str] = mapped_column(String(512))
    title: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    thumbnail_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_view_count: Mapped[int] = mapped_column(Integer, default=0)
    last_updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    user: Mapped[User] = relationship("User", back_populates="videos")
    channel: Mapped[Optional[Channel]] = relationship("Channel", back_populates="videos")
    monthly_views: Mapped[list[MonthlyView]] = relationship("MonthlyView", back_populates="video", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("video_id", name="uq_video_id"),
        Index("ix_user_video", "user_id", "video_id"),
        Index("ix_channel_video", "channel_id", "video_id"),
    )


class MonthlyView(Base):
    __tablename__ = "monthly_views"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    views: Mapped[int] = mapped_column(Integer, default=0)
    views_change: Mapped[int] = mapped_column(Integer, default=0)  # Change from previous update
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))

    user: Mapped[User] = relationship("User", back_populates="monthly_views")
    video: Mapped[Video] = relationship("Video", back_populates="monthly_views")

    __table_args__ = (
        UniqueConstraint("video_id", "year", "month", name="uq_video_year_month"),
        Index("ix_user_year_month", "user_id", "year", "month"),
        Index("ix_video_year_month", "video_id", "year", "month"),
    )


