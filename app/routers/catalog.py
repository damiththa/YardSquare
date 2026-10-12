"""
Botanical Species Catalog Router (`app/routers/catalog.py`).
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query

from app.models import PlantCategory, SpeciesCatalogItem
from app.services.plant_catalog import get_species_by_id, list_species_catalog

router = APIRouter(prefix="/api/v1/catalog", tags=["2. Botanical Species Catalog"])


@router.get("/species", response_model=List[SpeciesCatalogItem])
def get_catalog_species(
    category: Optional[PlantCategory] = Query(default=None, description="Filter by plant category"),
    q: Optional[str] = Query(default=None, description="Search by common/scientific name or care notes"),
) -> List[SpeciesCatalogItem]:
    """Return curated residential trees, fruit trees, shrubs, perennials, and vegetables."""
    return list_species_catalog(category=category, query=q)


@router.get("/species/{species_id}", response_model=SpeciesCatalogItem)
def get_single_species(species_id: str) -> SpeciesCatalogItem:
    """Return botanical metadata for a specific species ID."""
    item = get_species_by_id(species_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"Species not found: {species_id}")
    return item
