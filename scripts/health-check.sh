#!/bin/bash

# DataBot Health Check Script
# Usage: ./scripts/health-check.sh [environment]

set -euo pipefail

# Configuration
ENVIRONMENT="${1:-production}"
COMPOSE_FILE="docker-compose.$ENVIRONMENT.yml"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Health check results
HEALTH_STATUS=0
HEALTH_REPORT=""

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

add_to_report() {
    HEALTH_REPORT="$HEALTH_REPORT\n$1"
}

check_docker_services() {
    log_info "Checking Docker services..."
    
    local services=(postgres redis bot celery-worker)
    local failed_services=()
    
    for service in "${services[@]}"; do
        if docker-compose -f "$COMPOSE_FILE" ps "$service" | grep -q "Up"; then
            if docker-compose -f "$COMPOSE_FILE" ps "$service" | grep -q "healthy\|Up"; then
                log_success "✓ $service is running"
                add_to_report "✓ $service: Healthy"
            else
                log_warning "⚠ $service is running but not healthy"
                add_to_report "⚠ $service: Running but unhealthy"
                HEALTH_STATUS=1
            fi
        else
            log_error "✗ $service is not running"
            add_to_report "✗ $service: Not running"
            failed_services+=("$service")
            HEALTH_STATUS=2
        fi
    done
    
    if [ ${#failed_services[@]} -eq 0 ]; then
        log_success "All Docker services are running"
    else
        log_error "Failed services: ${failed_services[*]}"
    fi
}

check_database_connectivity() {
    log_info "Checking database connectivity..."
    
    if docker-compose -f "$COMPOSE_FILE" exec -T bot \
        python -c "from app.infrastructure.db import check_database_health; exit(0 if check_database_health() else 1)" 2>/dev/null; then
        log_success "✓ Database connectivity OK"
        add_to_report "✓ Database: Connected"
    else
        log_error "✗ Database connectivity failed"
        add_to_report "✗ Database: Connection failed"
        HEALTH_STATUS=2
    fi
}

check_redis_connectivity() {
    log_info "Checking Redis connectivity..."
    
    if docker-compose -f "$COMPOSE_FILE" exec -T redis redis-cli ping | grep -q "PONG"; then
        log_success "✓ Redis connectivity OK"
        add_to_report "✓ Redis: Connected"
    else
        log_error "✗ Redis connectivity failed"
        add_to_report "✗ Redis: Connection failed"
        HEALTH_STATUS=1  # Non-critical failure
    fi
}

check_bot_functionality() {
    log_info "Checking bot functionality..."
    
    # Check if bot process is running
    if docker-compose -f "$COMPOSE_FILE" exec -T bot pgrep -f "python start.py" >/dev/null; then
        log_success "✓ Bot process is running"
        add_to_report "✓ Bot: Process active"
    else
        log_error "✗ Bot process is not running"
        add_to_report "✗ Bot: Process not found"
        HEALTH_STATUS=2
    fi
    
    # Check Discord connection (if possible)
    if docker-compose -f "$COMPOSE_FILE" exec -T bot \
        timeout 10 python -c "
import asyncio
import discord
from app.config import settings
async def test():
    if not settings.discord_bot_token or settings.discord_bot_token.startswith('your_'):
        return False
    try:
        client = discord.Client(intents=discord.Intents.default())
        await client.login(settings.discord_bot_token)
        await client.close()
        return True
    except:
        return False
print('SUCCESS' if asyncio.run(test()) else 'FAILED')
" 2>/dev/null | grep -q "SUCCESS"; then
        log_success "✓ Discord authentication OK"
        add_to_report "✓ Discord: Authentication successful"
    else
        log_warning "⚠ Discord authentication check failed or token not configured"
        add_to_report "⚠ Discord: Authentication failed or not configured"
        HEALTH_STATUS=1
    fi
}

check_celery_workers() {
    log_info "Checking Celery workers..."
    
    if docker-compose -f "$COMPOSE_FILE" exec -T celery-worker \
        celery -A app.tasks.celery_app inspect ping 2>/dev/null | grep -q "pong"; then
        log_success "✓ Celery workers responding"
        add_to_report "✓ Celery: Workers active"
    else
        log_error "✗ Celery workers not responding"
        add_to_report "✗ Celery: Workers not responding"
        HEALTH_STATUS=2
    fi
}

check_disk_space() {
    log_info "Checking disk space..."
    
    local disk_usage=$(df -h / | awk 'NR==2 {print $5}' | sed 's/%//')
    
    if [ "$disk_usage" -lt 80 ]; then
        log_success "✓ Disk space OK ($disk_usage% used)"
        add_to_report "✓ Disk Space: $disk_usage% used"
    elif [ "$disk_usage" -lt 90 ]; then
        log_warning "⚠ Disk space warning ($disk_usage% used)"
        add_to_report "⚠ Disk Space: $disk_usage% used (warning)"
        HEALTH_STATUS=1
    else
        log_error "✗ Disk space critical ($disk_usage% used)"
        add_to_report "✗ Disk Space: $disk_usage% used (critical)"
        HEALTH_STATUS=2
    fi
}

check_memory_usage() {
    log_info "Checking memory usage..."
    
    local memory_usage=$(free | grep Mem | awk '{printf "%.0f", $3/$2 * 100.0}')
    
    if [ "$memory_usage" -lt 80 ]; then
        log_success "✓ Memory usage OK ($memory_usage% used)"
        add_to_report "✓ Memory: $memory_usage% used"
    elif [ "$memory_usage" -lt 90 ]; then
        log_warning "⚠ Memory usage warning ($memory_usage% used)"
        add_to_report "⚠ Memory: $memory_usage% used (warning)"
        HEALTH_STATUS=1
    else
        log_error "✗ Memory usage critical ($memory_usage% used)"
        add_to_report "✗ Memory: $memory_usage% used (critical)"
        HEALTH_STATUS=2
    fi
}

check_log_files() {
    log_info "Checking log files..."
    
    local log_dir="$PROJECT_DIR/logs"
    local log_file="$log_dir/bot.log"
    
    if [ -f "$log_file" ]; then
        local log_size=$(du -h "$log_file" | cut -f1)
        local error_count=$(tail -100 "$log_file" | grep -c "ERROR" || echo "0")
        
        log_success "✓ Log file exists ($log_size)"
        add_to_report "✓ Logs: Available ($log_size, $error_count recent errors)"
        
        if [ "$error_count" -gt 10 ]; then
            log_warning "⚠ High error count in recent logs ($error_count errors)"
            HEALTH_STATUS=1
        fi
    else
        log_warning "⚠ Log file not found"
        add_to_report "⚠ Logs: File not found"
        HEALTH_STATUS=1
    fi
}

check_network_connectivity() {
    log_info "Checking network connectivity..."
    
    # Check external connectivity
    if curl -s --max-time 10 https://www.googleapis.com/youtube/v3/ >/dev/null; then
        log_success "✓ YouTube API reachable"
        add_to_report "✓ Network: YouTube API accessible"
    else
        log_error "✗ YouTube API unreachable"
        add_to_report "✗ Network: YouTube API inaccessible"
        HEALTH_STATUS=2
    fi
    
    # Check Discord API
    if curl -s --max-time 10 https://discord.com/api/v10/gateway >/dev/null; then
        log_success "✓ Discord API reachable"
        add_to_report "✓ Network: Discord API accessible"
    else
        log_error "✗ Discord API unreachable"
        add_to_report "✗ Network: Discord API inaccessible"
        HEALTH_STATUS=2
    fi
}

generate_report() {
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    local status_text=""
    
    case $HEALTH_STATUS in
        0) status_text="HEALTHY" ;;
        1) status_text="WARNING" ;;
        2) status_text="CRITICAL" ;;
    esac
    
    echo ""
    echo "======================================"
    echo "DataBot Health Check Report"
    echo "======================================"
    echo "Environment: $ENVIRONMENT"
    echo "Timestamp: $timestamp"
    echo "Overall Status: $status_text"
    echo "======================================"
    echo -e "$HEALTH_REPORT"
    echo "======================================"
    
    # Save report to file
    local report_file="$PROJECT_DIR/logs/health-check-$(date +%Y%m%d-%H%M%S).log"
    {
        echo "DataBot Health Check Report"
        echo "Environment: $ENVIRONMENT"
        echo "Timestamp: $timestamp"
        echo "Overall Status: $status_text"
        echo -e "$HEALTH_REPORT"
    } > "$report_file"
    
    log_info "Report saved to: $report_file"
}

# Main execution
main() {
    cd "$PROJECT_DIR"
    
    log_info "Starting health check for $ENVIRONMENT environment..."
    echo ""
    
    # Run all health checks
    check_docker_services
    check_database_connectivity
    check_redis_connectivity
    check_bot_functionality
    check_celery_workers
    check_disk_space
    check_memory_usage
    check_log_files
    check_network_connectivity
    
    # Generate and display report
    generate_report
    
    # Exit with appropriate status code
    exit $HEALTH_STATUS
}

# Show usage if no environment specified and file called directly
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    if [[ $# -eq 0 ]]; then
        echo "Usage: $0 [environment]"
        echo "Environment: staging, production (default: production)"
        echo ""
        echo "Exit codes:"
        echo "  0 - Healthy"
        echo "  1 - Warning (some issues but not critical)"
        echo "  2 - Critical (major issues requiring attention)"
        exit 1
    fi
    
    main "$@"
fi
