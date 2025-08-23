# DataBot

A Discord bot for tracking YouTube channel and video statistics with automatic monthly reporting.

## Features

- **YouTube Statistics Tracking**: Monitor view counts, likes, and comments for YouTube videos
- **Automatic Monthly Reports**: Generate comprehensive monthly performance summaries
- **Discord Integration**: Easy-to-use Discord commands for managing channels and viewing stats
- **Database Storage**: Persistent storage of historical data with PostgreSQL
- **Caching**: Redis-based caching for improved performance
- **Background Tasks**: Automated data collection and report generation

## Architecture

```
app/
├── bot/              # Discord bot implementation
├── infrastructure/   # Database and cache connections
├── services/         # YouTube API integration
├── tasks/           # Background task processing
├── utils/           # Utility functions
└── models.py        # Database models
```

## Quick Start

### Option 1: Docker Deployment (Recommended)

1. **Clone and Setup**
   ```bash
   git clone <repository-url>
   cd salmonbot
   cp env.example .env
   ```

2. **Configure Environment**
   ```bash
   # Edit .env with your credentials
   nano .env
   ```

3. **Deploy with Docker**
   ```bash
   docker-compose up -d
   ```

4. **Check Status**
   ```bash
   docker-compose ps
   docker-compose logs -f bot
   ```

### Option 2: Local Development

1. **Install Dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Setup Database & Redis**
   ```bash
   # Start services with Docker
   docker-compose up -d postgres redis
   ```

3. **Configure Environment**
   ```bash
   cp env.example .env
   # Edit .env with your credentials
   ```

4. **Start the Bot**
   ```bash
   python start.py
   ```

## Environment Variables

### Required
- `DISCORD_BOT_TOKEN`: Your Discord bot token
- `YOUTUBE_API_KEY`: YouTube Data API v3 key

### Database & Cache
- `DATABASE_URL`: PostgreSQL connection string
- `REDIS_URL`: Redis connection string

### Optional
- `ENVIRONMENT`: Set to "production" for production deployment
- `LOG_LEVEL`: Logging level (DEBUG, INFO, WARNING, ERROR)
- `RATE_LIMIT_REQUESTS`: API rate limiting requests per window
- `RATE_LIMIT_WINDOW`: Rate limiting time window in seconds
- `CACHE_TTL`: Cache time-to-live in seconds
- `MAX_VIDEOS_PER_USER`: Maximum videos per user (default: 100)

## Development

### Running Tests
```bash
pytest tests/
```

### Code Quality
```bash
# Format code
black app/ tests/

# Lint code
flake8 app/ tests/

# Type checking
mypy app/
```

## Production Deployment

### Health Checks
The application includes health checks for:
- Database connectivity
- Redis connectivity
- Bot functionality

### Monitoring
- Logs are written to `logs/bot.log` with rotation
- Structured JSON logging in production
- Health check endpoints available

### Scaling
- Celery workers can be scaled: `docker-compose up --scale celery-worker=3`
- Database connection pooling configured
- Redis connection pooling with fallback

## CI/CD Pipeline

DataBot includes a comprehensive CI/CD pipeline with GitHub Actions:

### Pipeline Features
- ✅ **Automated Testing** - Unit tests, integration tests, code coverage
- ✅ **Code Quality Checks** - Black formatting, Flake8 linting, MyPy type checking
- ✅ **Security Scanning** - Trivy vulnerability scanner, dependency checks
- ✅ **Multi-Platform Builds** - Docker images for AMD64 and ARM64
- ✅ **Automated Deployments** - Staging and production environments
- ✅ **Zero-Downtime Deployments** - Rolling updates with health checks
- ✅ **Automatic Rollbacks** - On deployment failure
- ✅ **Monitoring & Alerting** - Slack notifications, health monitoring

### Deployment Workflow

```bash
# Development → Staging
git push origin develop
# → Triggers automatic deployment to staging

# Staging → Production  
git checkout main
git merge develop
git push origin main
# → Requires approval, deploys to production

# Manual deployment
./scripts/deploy.sh deploy production v1.2.3

# Health checks
./scripts/health-check.sh production

# Emergency rollback
./scripts/deploy.sh rollback production
```

### Environment Management
- **Staging**: `develop` branch → `staging` environment
- **Production**: `main` branch → `production` environment
- **Feature branches**: Run tests only

For detailed deployment instructions, see [DEPLOYMENT.md](DEPLOYMENT.md).

## License

MIT License
