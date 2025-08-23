-- YouTubeBot Database Initialization Script
-- This script creates the complete database schema for the YouTube video tracking bot

-- Enable UUID extension for future use
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Create custom types if needed
DO $$ BEGIN
    CREATE TYPE verification_mode AS ENUM ('manual', 'automatic');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Drop existing tables if they exist (safe for re-runs)
DROP TABLE IF EXISTS monthly_views CASCADE;
DROP TABLE IF EXISTS videos CASCADE;
DROP TABLE IF EXISTS channels CASCADE;
DROP TABLE IF EXISTS users CASCADE;

-- Users table - Discord user information
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    discord_user_id VARCHAR(64) NOT NULL,
    discord_username VARCHAR(128),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_users_discord_user_id UNIQUE (discord_user_id)
);

-- Channels table - YouTube channels being tracked
CREATE TABLE channels (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel_id VARCHAR(128) NOT NULL,
    channel_name VARCHAR(256),
    url VARCHAR(512) NOT NULL,
    verification_code VARCHAR(16) NOT NULL,
    is_verified BOOLEAN NOT NULL DEFAULT FALSE,
    verification_mode VARCHAR(16) NOT NULL DEFAULT 'manual',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_sync_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_channel_id UNIQUE (channel_id),
    CONSTRAINT chk_verification_mode CHECK (verification_mode IN ('manual', 'automatic'))
);

-- Videos table - YouTube videos being tracked
CREATE TABLE videos (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    channel_id INTEGER REFERENCES channels(id) ON DELETE SET NULL,
    video_id VARCHAR(128) NOT NULL,
    url VARCHAR(512) NOT NULL,
    title VARCHAR(512),
    description TEXT,
    thumbnail_url VARCHAR(512),
    published_at TIMESTAMP,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_view_count INTEGER NOT NULL DEFAULT 0,
    last_like_count INTEGER DEFAULT 0,
    last_comment_count INTEGER DEFAULT 0,
    last_updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_video_id UNIQUE (video_id)
);

-- Monthly views table - Historical view data
CREATE TABLE monthly_views (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    year INTEGER NOT NULL,
    month INTEGER NOT NULL,
    views INTEGER NOT NULL DEFAULT 0,
    views_change INTEGER NOT NULL DEFAULT 0,
    likes INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_video_year_month UNIQUE (video_id, year, month),
    CONSTRAINT chk_year CHECK (year >= 2020 AND year <= 2030),
    CONSTRAINT chk_month CHECK (month >= 1 AND month <= 12)
);

-- Create indexes for optimal performance
CREATE INDEX ix_users_discord_user_id ON users (discord_user_id);
CREATE INDEX ix_users_created_at ON users (created_at);

CREATE INDEX ix_channels_channel_id ON channels (channel_id);
CREATE INDEX ix_channels_user_id ON channels (user_id);
CREATE INDEX ix_channels_user_channel ON channels (user_id, channel_id);
CREATE INDEX ix_channels_is_verified ON channels (is_verified);
CREATE INDEX ix_channels_is_active ON channels (is_active);
CREATE INDEX ix_channels_created_at ON channels (created_at);

CREATE INDEX ix_videos_channel_id ON videos (channel_id);
CREATE INDEX ix_videos_user_id ON videos (user_id);
CREATE INDEX ix_videos_video_id ON videos (video_id);
CREATE INDEX ix_videos_user_video ON videos (user_id, video_id);
CREATE INDEX ix_videos_channel_video ON videos (channel_id, video_id);
CREATE INDEX ix_videos_is_active ON videos (is_active);
CREATE INDEX ix_videos_published_at ON videos (published_at);
CREATE INDEX ix_videos_last_updated_at ON videos (last_updated_at);

CREATE INDEX ix_monthly_views_user_id ON monthly_views (user_id);
CREATE INDEX ix_monthly_views_video_id ON monthly_views (video_id);
CREATE INDEX ix_monthly_views_user_year_month ON monthly_views (user_id, year, month);
CREATE INDEX ix_monthly_views_video_year_month ON monthly_views (video_id, year, month);
CREATE INDEX ix_monthly_views_year_month ON monthly_views (year, month);

-- Create updated_at trigger function
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Create triggers for updated_at columns
CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_channels_updated_at BEFORE UPDATE ON channels
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_videos_updated_at BEFORE UPDATE ON videos
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Grant permissions to the application user
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO youtubebot;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO youtubebot;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO youtubebot;

-- Create a view for easy stats querying
CREATE OR REPLACE VIEW video_stats AS
SELECT 
    v.id,
    v.video_id,
    v.title,
    v.url,
    v.last_view_count,
    v.last_like_count,
    v.last_comment_count,
    v.last_updated_at,
    c.channel_name,
    u.discord_username,
    mv.year,
    mv.month,
    mv.views,
    mv.views_change
FROM videos v
LEFT JOIN channels c ON v.channel_id = c.id
LEFT JOIN users u ON v.user_id = u.id
LEFT JOIN monthly_views mv ON v.id = mv.video_id
WHERE v.is_active = TRUE;

-- Insert some sample data for testing (optional)
-- INSERT INTO users (discord_user_id, discord_username) VALUES ('123456789', 'TestUser');
-- INSERT INTO channels (user_id, channel_id, channel_name, url, verification_code, is_verified, verification_mode) 
-- VALUES (1, 'UC123456789', 'Test Channel', 'https://youtube.com/@testchannel', 'TEST123', TRUE, 'manual');

-- Log completion
DO $$
BEGIN
    RAISE NOTICE 'YouTubeBot database schema initialized successfully!';
END $$;
