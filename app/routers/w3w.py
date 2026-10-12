"""
what3words On-Demand Coordinate & Grid Square Router (`app/routers/w3w.py`).
"""

from typing import Any, Dict, Optional
from fastapi import APIRouter, Query

from app.config import get_settings
from app.services.geo_grid import format_cell_label, grid_cell_to_lat_lng
from app.services.w3w_service import get_w3w_service_status, resolve_w3w_sync

router = APIRouter(prefix="/api/v1/w3w", tags=["3. What3Words 3m Grid Resolver"])


@router.get("/convert-to-3wa")
def convert_cell_or_coords_to_3wa(
    lat: Optional[float] = Query(default=None, description="WGS84 latitude"),
    lng: Optional[float] = Query(default=None, description="WGS84 longitude"),
    parcel_center_lat: Optional[float] = Query(default=None),
    parcel_center_lng: Optional[float] = Query(default=None),
    row: int = Query(default=8, ge=0, le=60),
    col: int = Query(default=12, ge=0, le=60),
) -> Dict[str, Any]:
    """Resolve a 3m x 3m grid cell or WGS84 coordinate into a what3words address (`///w1.w2.w3`)."""
    settings = get_settings()
    c_lat = parcel_center_lat if parcel_center_lat is not None else settings.DEFAULT_FALLBACK_LAT
    c_lng = parcel_center_lng if parcel_center_lng is not None else settings.DEFAULT_FALLBACK_LNG

    if lat is None or lng is None:
        lat, lng = grid_cell_to_lat_lng(c_lat, c_lng, row, col)

    w3w = resolve_w3w_sync(lat, lng)
    return {
        "row": row,
        "col": col,
        "label": format_cell_label(row, col),
        "lat": lat,
        "lng": lng,
        "w3w": w3w,
        "w3w_service": get_w3w_service_status(),
    }


@router.get("/status")
def w3w_status() -> Dict[str, Any]:
    """Return current what3words API configuration and circuit-breaker status."""
    return get_w3w_service_status()
