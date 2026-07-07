"""
FastAPI Main Application
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import os
import uvicorn

from database import get_db
from config import settings
from logging_config import setup_logging, get_logger

# Setup logging
setup_logging("INFO")
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown"""
    # Startup: Initialize database connection
    logger.info("[STARTUP] Starting FastAPI Dashboard API...")
    db = get_db()
    logger.info(f"[OK] Connected to InfluxDB at {db.url}")
    
    yield
    
    # Shutdown: Close database connection
    logger.info("[SHUTDOWN] Closing connections...")
    db.close()


# Create FastAPI app
app = FastAPI(
    title=settings.api_title,
    description="REST API for supply chain digital twin dashboard",
    version=settings.api_version,
    lifespan=lifespan
)

# Configure CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=settings.cors_allow_methods,
    allow_headers=settings.cors_allow_headers,
)


# ===== Health Check =====

@app.get("/")
async def root():
    """Root endpoint - API information"""
    return {
        "service": "Digital Twin Dashboard API",
        "version": "1.0.0",
        "status": "running"
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        db = get_db()
        # Simple check that we can access the database
        return {
            "status": "healthy",
            "influxdb": "connected",
            "url": db.url,
            "org": db.org,
            "bucket": db.bucket
        }
    except (ConnectionError, TimeoutError) as e:
        # Network/connection issues
        logger.warning(f"Health check failed - connection error: {e}")
        return {
            "status": "unhealthy",
            "error": f"Database connection failed: {str(e)}"
        }
    except (ValueError, KeyError, AttributeError) as e:
        # Configuration issues
        logger.error(f"Health check failed - config error: {e}")
        return {
            "status": "unhealthy",
            "error": f"Configuration error: {str(e)}"
        }
    except Exception as e:
        # Unexpected errors
        logger.error(f"UNEXPECTED health check error: {type(e).__name__}: {e}")
        return {
            "status": "unhealthy",
            "error": str(e)
        }


# Import and register routers
from routers import simulations, trucks, kpis, timeseries, events, dashboard, ai_insights, scenarios

app.include_router(simulations.router, prefix="/api", tags=["Simulations"])
app.include_router(trucks.router, prefix="/api", tags=["Trucks"])
app.include_router(kpis.router, prefix="/api", tags=["KPIs"])
app.include_router(timeseries.router, prefix="/api", tags=["Time Series"])
app.include_router(events.router, prefix="/api", tags=["Events"])
app.include_router(dashboard.router, prefix="/api", tags=["Dashboard"])
app.include_router(ai_insights.router, prefix="/api", tags=["AI Insights"])
app.include_router(scenarios.router, prefix="/api", tags=["Scenarios"])


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
