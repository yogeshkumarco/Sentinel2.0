#!/usr/bin/env python3
"""
Sentinel V3 - Entry Point
Starts the FastAPI server with BotManager for multi-exchange trading
"""

import uvicorn
import sys
import os

# Add parent directory to path for module resolution
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    print("=" * 60)
    print("🦅 SENTINEL V3 - CRYPTO TRADING BOT")
    print("=" * 60)
    print("Starting FastAPI server on http://127.0.0.1:8000")
    print("API Documentation: http://127.0.0.1:8000/docs")
    print("=" * 60)
    
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,  # Auto-reload on code changes
        log_level="info"
    )
