#!/bin/bash

# DataBot Deployment Script
# Usage: ./scripts/deploy.sh [staging|production] [version]

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="$PROJECT_DIR/backups"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_requirements() {
    log_info "Checking requirements..."
    
    # Check if docker is installed
    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed"
        exit 1
    fi
    
    # Check if docker-compose is installed
    if ! command -v docker-compose &> /dev/null; then
        log_error "docker-compose is not installed"
        exit 1
    fi
    
    log_success "Requirements check passed"
}

load_environment() {
    local env_file="$1"
    
    if [[ ! -f "$env_file" ]]; then
        log_error "Environment file not found: $env_file"
        exit 1
    fi
    
    log_info "Loading environment from $env_file"
    export $(grep -v '^#' "$env_file" | xargs)
}

create_backup() {
    local environment="$1"
    local timestamp=$(date +%Y%m%d_%H%M%S)
    
    log_info "Creating backup for $environment environment..."
    
    mkdir -p "$BACKUP_DIR"
    
    # Database backup
    if docker-compose -f "docker-compose.$environment.yml" ps postgres | grep -q "Up"; then
        docker-compose -f "docker-compose.$environment.yml" exec -T postgres \
            pg_dump -U databot databot > "$BACKUP_DIR/databot_${environment}_${timestamp}.sql"
        log_success "Database backup created: databot_${environment}_${timestamp}.sql"
    else
        log_warning "PostgreSQL service is not running, skipping database backup"
    fi
    
    # Configuration backup
    tar -czf "$BACKUP_DIR/config_${environment}_${timestamp}.tar.gz" \
        .env."$environment" docker-compose."$environment".yml
    log_success "Configuration backup created: config_${environment}_${timestamp}.tar.gz"
}

deploy() {
    local environment="$1"
    local version="${2:-latest}"
    local compose_file="docker-compose.$environment.yml"
    
    log_info "Starting deployment to $environment environment with version $version"
    
    # Check if compose file exists
    if [[ ! -f "$compose_file" ]]; then
        log_error "Compose file not found: $compose_file"
        exit 1
    fi
    
    # Set image tag
    export IMAGE_TAG="ghcr.io/yourusername/databot:$version"
    
    # Load environment variables
    load_environment ".env.$environment"
    
    # Create backup
    create_backup "$environment"
    
    # Pull latest images
    log_info "Pulling Docker images..."
    docker-compose -f "$compose_file" pull
    
    # Start database and cache first
    log_info "Starting infrastructure services..."
    docker-compose -f "$compose_file" up -d postgres redis
    
    # Wait for infrastructure to be ready
    log_info "Waiting for infrastructure to be ready..."
    sleep 30
    
    # Deploy application services with rolling update
    if [[ "$environment" == "production" ]]; then
        # Production: Rolling deployment
        log_info "Performing rolling deployment..."
        
        # Scale up new instances
        docker-compose -f "$compose_file" up -d --scale bot=2 --no-recreate bot
        sleep 30
        
        # Check health of new instance
        if docker-compose -f "$compose_file" ps | grep -q "Up (healthy)"; then
            log_success "New instance is healthy"
            
            # Scale down to single instance
            docker-compose -f "$compose_file" up -d --scale bot=1 --no-recreate bot
            
            # Update other services
            docker-compose -f "$compose_file" up -d --no-deps celery-worker
        else
            log_error "New instance failed health check, rolling back"
            docker-compose -f "$compose_file" up -d --scale bot=1 --no-recreate bot
            exit 1
        fi
    else
        # Staging: Simple deployment
        log_info "Deploying to staging..."
        docker-compose -f "$compose_file" up -d --no-deps bot celery-worker
    fi
    
    # Wait for services to be ready
    log_info "Waiting for services to be ready..."
    sleep 60
    
    # Health checks
    health_check "$environment"
    
    # Cleanup old images
    cleanup_old_images
    
    log_success "Deployment to $environment completed successfully!"
}

health_check() {
    local environment="$1"
    local compose_file="docker-compose.$environment.yml"
    
    log_info "Running health checks..."
    
    # Check container health
    if ! docker-compose -f "$compose_file" ps | grep -q "Up (healthy)"; then
        log_error "Health check failed - containers are not healthy"
        docker-compose -f "$compose_file" logs --tail=50
        return 1
    fi
    
    # Check database connectivity
    if ! docker-compose -f "$compose_file" exec -T bot \
        python -c "from app.infrastructure.db import check_database_health; assert check_database_health()"; then
        log_error "Database connectivity check failed"
        return 1
    fi
    
    # Check Redis connectivity
    if ! docker-compose -f "$compose_file" exec -T bot \
        python -c "from app.infrastructure.cache import check_redis_health; assert check_redis_health()"; then
        log_warning "Redis connectivity check failed (non-critical)"
    fi
    
    log_success "Health checks passed"
}

cleanup_old_images() {
    log_info "Cleaning up old Docker images..."
    
    # Remove dangling images
    docker image prune -f
    
    # Keep only last 5 versions of our images
    docker images --format "table {{.Repository}}:{{.Tag}}\t{{.CreatedAt}}" | \
        grep "ghcr.io/yourusername/databot" | \
        tail -n +6 | \
        awk '{print $1}' | \
        xargs -r docker rmi
    
    log_success "Image cleanup completed"
}

rollback() {
    local environment="$1"
    local compose_file="docker-compose.$environment.yml"
    
    log_warning "Starting rollback for $environment environment..."
    
    # Find the most recent backup image
    local rollback_image=$(docker images --format "table {{.Repository}}:{{.Tag}}" | \
        grep "databot:rollback" | head -1 | awk '{print $1}')
    
    if [[ -z "$rollback_image" ]]; then
        log_error "No rollback image found"
        exit 1
    fi
    
    log_info "Rolling back to image: $rollback_image"
    
    # Update image tag for rollback
    export IMAGE_TAG="$rollback_image"
    
    # Deploy the rollback image
    docker-compose -f "$compose_file" up -d --no-deps bot celery-worker
    
    # Wait and check health
    sleep 30
    health_check "$environment"
    
    log_success "Rollback completed successfully"
}

show_usage() {
    echo "Usage: $0 <command> [environment] [version]"
    echo ""
    echo "Commands:"
    echo "  deploy <staging|production> [version]  - Deploy to specified environment"
    echo "  rollback <staging|production>          - Rollback to previous version"
    echo "  health <staging|production>            - Run health checks"
    echo "  backup <staging|production>            - Create backup"
    echo "  logs <staging|production> [service]    - Show logs"
    echo ""
    echo "Examples:"
    echo "  $0 deploy staging"
    echo "  $0 deploy production v1.2.3"
    echo "  $0 rollback production"
    echo "  $0 health staging"
    echo "  $0 logs production bot"
}

# Main script
main() {
    cd "$PROJECT_DIR"
    
    case "${1:-}" in
        deploy)
            if [[ $# -lt 2 ]]; then
                log_error "Environment required for deploy command"
                show_usage
                exit 1
            fi
            check_requirements
            deploy "$2" "${3:-latest}"
            ;;
        rollback)
            if [[ $# -lt 2 ]]; then
                log_error "Environment required for rollback command"
                show_usage
                exit 1
            fi
            check_requirements
            rollback "$2"
            ;;
        health)
            if [[ $# -lt 2 ]]; then
                log_error "Environment required for health command"
                show_usage
                exit 1
            fi
            health_check "$2"
            ;;
        backup)
            if [[ $# -lt 2 ]]; then
                log_error "Environment required for backup command"
                show_usage
                exit 1
            fi
            create_backup "$2"
            ;;
        logs)
            if [[ $# -lt 2 ]]; then
                log_error "Environment required for logs command"
                show_usage
                exit 1
            fi
            docker-compose -f "docker-compose.$2.yml" logs -f "${3:-}"
            ;;
        *)
            show_usage
            exit 1
            ;;
    esac
}

main "$@"
