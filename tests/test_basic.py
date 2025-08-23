"""
Basic tests for DataBot core functionality.
"""

import pytest
from unittest.mock import Mock, patch

from app.config import settings
from app.models import User, Channel, Video
from app.infrastructure.db import init_db, session_scope


class TestConfiguration:
    """Test configuration loading."""
    
    def test_settings_loaded(self):
        """Test that settings are properly loaded."""
        assert hasattr(settings, 'discord_bot_token')
        assert hasattr(settings, 'database_url')
        assert hasattr(settings, 'youtube_api_key')


class TestModels:
    """Test database models."""
    
    def test_user_model(self):
        """Test User model creation."""
        user = User(
            discord_user_id="123456789",
            discord_username="testuser"
        )
        assert user.discord_user_id == "123456789"
        assert user.discord_username == "testuser"
    
    def test_channel_model(self):
        """Test Channel model creation."""
        channel = Channel(
            user_id=1,
            channel_id="UC_test",
            channel_name="Test Channel"
        )
        assert channel.channel_id == "UC_test"
        assert channel.channel_name == "Test Channel"
    
    def test_video_model(self):
        """Test Video model creation."""
        video = Video(
            user_id=1,
            channel_id=1,
            video_id="dQw4w9WgXcQ",
            title="Test Video"
        )
        assert video.video_id == "dQw4w9WgXcQ"
        assert video.title == "Test Video"


class TestDatabase:
    """Test database operations."""
    
    @patch('app.infrastructure.db.wait_for_database')
    @patch('app.infrastructure.db.engine.connect')
    @patch('app.infrastructure.db.Base.metadata.create_all')
    def test_database_initialization(self, mock_create_all, mock_engine_connect, mock_wait_for_db):
        """Test database initialization."""
        # Mock database availability
        mock_wait_for_db.return_value = True
        
        # Mock engine connection context manager
        mock_connection = Mock()
        mock_connection.execute.return_value.fetchall.return_value = [('users',), ('channels',), ('videos',)]
        mock_engine_connect.return_value.__enter__.return_value = mock_connection
        mock_engine_connect.return_value.__exit__.return_value = None
        
        init_db()
        mock_wait_for_db.assert_called_once()
        mock_create_all.assert_called_once()
