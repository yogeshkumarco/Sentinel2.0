"""
Sentinel V3 - FastAPI Main Application
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import logging

from app.api import endpoints
from app.engine.services import get_bot_manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Sentinel")

app = FastAPI(
    title="Sentinel V3 API",
    description="Multi-Exchange Crypto Trading Bot with Harmonic Pattern Detection",
    version="3.0.0"
)

# CORS Middleware (Allow React frontend)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify exact origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API router
app.include_router(endpoints.router, prefix="/api")


@app.on_event("startup")
async def startup_event():
    """Initialize BotManager on startup"""
    logger.info("=" * 60)
    logger.info("🦅 SENTINEL V3 - STARTING")
    logger.info("=" * 60)
    
    # Initialize BotManager (singleton pattern)
    manager = get_bot_manager()
    logger.info(f"✅ BotManager ready with exchanges: {list(manager.bots.keys())}")
    
    logger.info("=" * 60)
    logger.info("📡 API Ready - Frontend can connect now")
    logger.info("=" * 60)


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("🛑 Shutting down Sentinel V3...")
    manager = get_bot_manager()
    
    # Stop all running bots
    for exchange in list(manager.bots.keys()):
        if manager.is_running(exchange):
            await manager.stop_bot(exchange)
    
    logger.info("👋 Goodbye!")


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "app": "Sentinel V3",
        "version": "3.0.0",
        "status": "online",
        "docs": "/docs"
    }
