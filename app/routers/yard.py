"""
Residential Yard, Parcel Border Adjustment & Botanical Grid Router (`app/routers/yard.py`).
"""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

from app.models import (
    CareLogCreateRequest,
    PlantCreateRequest,
    PlantPhoto,
    YardAnalysisRequest,
    YardMap,
    YardSafetyAndHoseReport,
)
from app.services.gcp_store import get_store
from app.services.geo_grid import (
    analyze_yard_layout,
    autocomplete_addresses,
    build_grid_square_coord,
    geocode_address_to_yard_parcel,
    lat_lng_to_grid_cell,
)

router = APIRouter(prefix="/api/v1/yard", tags=["1. Yard Parcel, Border & Plant Tracker"])


class GeocodeRequest(BaseModel):
    street_address: str
    fallback_lat: Optional[float] = None
    fallback_lng: Optional[float] = None
    apply_to_yard_id: Optional[str] = None


class GpsToSquareRequest(BaseModel):
    parcel_center_lat: float
    parcel_center_lng: float
    user_lat: float
    user_lng: float


@router.get("/autocomplete")
def address_autocomplete(
    q: str = Query(..., description="Partial street address typed by homeowner"),
    limit: int = Query(default=5, ge=1, le=10),
) -> Dict[str, Any]:
    """Stage 1 Split-Provider Autocomplete ($0.00 cost, 0 Google Maps quota used)."""
    return autocomplete_addresses(query=q, limit=limit)


@router.post("/geocode")
def geocode_and_extract_parcel(req: GeocodeRequest) -> Dict[str, Any]:
    """
    Stage 2 Rooftop Geocode + OSM Overpass Building/Parcel Footprint + USDA Hardiness Zone.
    Optionally updates the target yard's address, center coordinates, and initial border zones.
    """
    parcel = geocode_address_to_yard_parcel(
        street_address=req.street_address,
        fallback_lat=req.fallback_lat,
        fallback_lng=req.fallback_lng,
    )
    if req.apply_to_yard_id:
        store = get_store()
        yard = store.get_yard(req.apply_to_yard_id)
        if yard:
            yard.street_address = parcel["formatted_address"]
            yard.parcel_center_lat = parcel["parcel_center_lat"]
            yard.parcel_center_lng = parcel["parcel_center_lng"]
            yard.usda_zone = parcel["usda_zone"]
            yard.usda_min_temp_f = parcel["usda_min_temp_f"]
            yard.zones = parcel["zones"]
            store.save_yard(yard)
    return parcel


@router.get("/list", response_model=List[YardMap])
def list_saved_yards() -> List[YardMap]:
    """Return all saved yards in the active environment (`dev_yards` or `prod_yards`)."""
    return get_store().list_yards()


@router.get("/{yard_id}", response_model=YardMap)
def get_yard_by_id(yard_id: str) -> YardMap:
    """Fetch a persisted YardMap including its adjusted border, 3m W3W squares, plants, and hose/safety analysis."""
    yard = get_store().get_yard(yard_id)
    if not yard:
        raise HTTPException(status_code=404, detail=f"Yard '{yard_id}' not found")
    return yard


@router.put("/{yard_id}", response_model=YardMap)
def update_yard_map(yard_id: str, yard_update: YardMap) -> YardMap:
    """Save full YardMap state (adjusted borders, spigots, waypoints, and planted items) to Firestore."""
    yard_update.yard_id = yard_id
    return get_store().save_yard(yard_update)


@router.post("/{yard_id}/analyze", response_model=YardSafetyAndHoseReport)
def analyze_yard(yard_id: str, req: YardAnalysisRequest) -> YardSafetyAndHoseReport:
    """Compute live garden hose reach, root-to-foundation/septic safety, canopy overhang, and sun compatibility."""
    return analyze_yard_layout(
        hose_length_ft=req.hose_length_ft,
        spigot_s1=req.spigot_s1,
        spigot_s2=req.spigot_s2,
        hose_waypoints=req.hose_waypoints,
        zones=req.zones,
        plants=req.plants,
        usda_zone=req.usda_zone,
    )


@router.post("/{yard_id}/plants", response_model=YardMap)
def add_plant(yard_id: str, req: PlantCreateRequest) -> YardMap:
    """Plant a tree, shrub, perennial, or raised bed into a specific 3m x 3m W3W square (and 3x3 sub-slot)."""
    try:
        return get_store().add_plant_to_yard(yard_id, req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{yard_id}/plants/{plant_id}", response_model=YardMap)
def delete_plant(yard_id: str, plant_id: str) -> YardMap:
    """Remove a planted item from the yard."""
    try:
        return get_store().remove_plant_from_yard(yard_id, plant_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{yard_id}/plants/{plant_id}/care", response_model=YardMap)
def log_care_event(yard_id: str, plant_id: str, req: CareLogCreateRequest) -> YardMap:
    """Record a 1-tap care event (watered, pruned, fertilized, mulched, harvested) on a planted item."""
    try:
        return get_store().log_plant_care(yard_id, plant_id, req)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{yard_id}/plants/{plant_id}/photos", response_model=PlantPhoto)
async def upload_plant_growth_photo(
    yard_id: str,
    plant_id: str,
    file: UploadFile = File(...),
    caption: Optional[str] = Form(default=None),
    season_tag: str = Form(default="summer"),
) -> PlantPhoto:
    """Upload a seasonal plant growth photo to Google Cloud Storage (`gs://{bucket}/{env}/{yard_id}/{plant_id}/...`)."""
    content = await file.read()
    return get_store().upload_plant_photo(
        yard_id=yard_id,
        plant_id=plant_id,
        filename=file.filename or "plant.jpg",
        content_bytes=content,
        content_type=file.content_type or "image/jpeg",
        caption=caption,
        season_tag=season_tag,
    )


@router.post("/gps-to-square")
def convert_phone_gps_to_square(req: GpsToSquareRequest) -> Dict[str, Any]:
    """Convert phone browser GPS coordinates while walking the yard into the nearest 3m x 3m W3W square."""
    row, col = lat_lng_to_grid_cell(
        parcel_center_lat=req.parcel_center_lat,
        parcel_center_lng=req.parcel_center_lng,
        lat=req.user_lat,
        lng=req.user_lng,
    )
    sq = build_grid_square_coord(req.parcel_center_lat, req.parcel_center_lng, row, col, resolve_w3w=True)
    return sq.model_dump()
