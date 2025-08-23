"""
Health check endpoint for Render deployment.
This provides a simple HTTP endpoint for health checks.
"""

import os
import threading
import time
from flask import Flask, jsonify
from app.infrastructure.db import check_database_health
from app.infrastructure.cache import check_redis_health
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Create Flask app
app = Flask(__name__)

# Health check status
health_status = {
    "status": "healthy",
    "timestamp": time.time(),
    "checks": {
        "database": False,
        "redis": False,
        "bot": False
    }
}

def update_health_status():
    """Update health status by checking all services"""
    try:
        # Check database
        health_status["checks"]["database"] = check_database_health()
        
        # Check Redis
        health_status["checks"]["redis"] = check_redis_health()
        
        # Check if bot is running (simple file-based check)
        bot_running = os.path.exists("/tmp/databot_running")
        health_status["checks"]["bot"] = bot_running
        
        # Overall status
        all_healthy = all(health_status["checks"].values())
        health_status["status"] = "healthy" if all_healthy else "unhealthy"
        health_status["timestamp"] = time.time()
        
        logger.info(f"Health check updated: {health_status}")
        
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        health_status["status"] = "error"
        health_status["error"] = str(e)

@app.route('/health')
def health_check():
    """Health check endpoint for Render"""
    update_health_status()
    
    if health_status["status"] == "healthy":
        return jsonify(health_status), 200
    else:
        return jsonify(health_status), 503

@app.route('/')
def root():
    """Root endpoint"""
    return jsonify({
        "service": "DataBot",
        "status": "running",
        "version": "1.0.0"
    }), 200

def start_health_server(host='0.0.0.0', port=8080):
    """Start the health check server in a separate thread"""
    def run_server():
        try:
            logger.info(f"Starting health check server on {host}:{port}")
            app.run(host=host, port=port, debug=False, use_reloader=False)
        except Exception as e:
            logger.error(f"Failed to start health server: {e}")
    
    # Start server in background thread
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    logger.info("Health check server started in background thread")

def mark_bot_running():
    """Mark that the bot is running"""
    try:
        with open("/tmp/databot_running", "w") as f:
            f.write(str(time.time()))
        logger.info("Bot running status marked")
    except Exception as e:
        logger.error(f"Failed to mark bot running: {e}")

def mark_bot_stopped():
    """Mark that the bot has stopped"""
    try:
        if os.path.exists("/tmp/databot_running"):
            os.remove("/tmp/databot_running")
        logger.info("Bot running status cleared")
    except Exception as e:
        logger.error(f"Failed to clear bot running status: {e}")
