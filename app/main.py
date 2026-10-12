"""
YardSquare - Main FastAPI Application Entrypoint (`app/main.py`).

Cloud-Direct on Google Cloud Platform (Cloud Run + Cloud Firestore + Cloud Storage + Secret Manager).
Tier-1 ($0.00 Personal Use) Free-Tier Guardrails, Zero Hardcoded Secrets, and Archify v3.0 Architecture.
"""

import os
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.routers import catalog, w3w, yard
from app.services.gcp_store import get_store
from app.services.geo_grid import get_maps_free_tier_status
from app.services.w3w_service import get_w3w_service_status

settings = get_settings()

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.API_VERSION,
    description=(
        "Residential Plant & Tree Yard Tracker on a 3m x 3m What3Words Property Grid with "
        "Actual Land & Building Footprint Extraction, Interactive Border Adjustment, 3x3 Sub-Square "
        "Garden Beds, Garden Hose Reach, and Root/Canopy Safety Analysis. Built directly on GCP."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(yard.router)
app.include_router(catalog.router)
app.include_router(w3w.router)

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/api/v1/health", tags=["0. System & GCP Environment"])
def health_check() -> Dict[str, Any]:
    """Cloud Run readiness/liveness probe, active GCP environment, and Tier-1 Free-Tier cap status."""
    store = get_store()
    return {
        "status": "healthy",
        "app_name": settings.APP_NAME,
        "version": settings.API_VERSION,
        "environment": settings.ENV,
        "is_prod": settings.is_prod,
        "gcp_project_configured": bool(settings.GCP_PROJECT_ID),
        "firestore_collection_prefix": settings.effective_collection_prefix,
        "gcs_bucket": settings.effective_gcs_bucket,
        "gcp_live_adc_connected": store.gcp_live_connected,
        "secret_manager_enabled": settings.USE_GCP_SECRET_MANAGER,
        "w3w_service": get_w3w_service_status(),
        "maps_free_tier_guardrail": get_maps_free_tier_status(),
    }


@app.get("/api/v1/config/public", tags=["0. System & GCP Environment"])
def get_public_browser_config() -> Dict[str, Any]:
    """Return safe public configuration for browser frontends (never exposes raw secret keys)."""
    return {
        "app_name": settings.APP_NAME,
        "environment": settings.ENV.upper(),
        "gcp_project_configured": bool(settings.GCP_PROJECT_ID),
        "google_maps_configured": bool(settings.google_maps_api_key),
        "w3w_live_api_configured": bool(settings.w3w_api_key),
        "grid_rows": settings.DEFAULT_GRID_ROWS,
        "grid_cols": settings.DEFAULT_GRID_COLS,
        "square_size_meters": settings.SQUARE_SIZE_METERS,
        "square_size_feet": round(settings.square_size_feet, 2),
        "maps_free_tier_guardrail": get_maps_free_tier_status(),
    }


@app.get("/", include_in_schema=False)
def serve_yard_companion() -> FileResponse:
    """Serve Main Interactive YardSquare Companion Portal (`app/static/index.html`)."""
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/walk", include_in_schema=False)
def serve_mobile_yard_walk() -> FileResponse:
    """Serve Mobile Backyard Walk & Offline Care Logger (`app/static/yard_walk.html`)."""
    return FileResponse(os.path.join(STATIC_DIR, "yard_walk.html"))


@app.get("/architecture", include_in_schema=False)
def serve_archify_architecture() -> FileResponse:
    """Serve Interactive Archify v3.0 System Architecture Diagram (`app/static/architecture.html`)."""
    return FileResponse(os.path.join(STATIC_DIR, "architecture.html"))
