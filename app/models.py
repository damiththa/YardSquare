"""
Pydantic Data Models for YardSquare (`app/models.py`).

Covers:
1. 3m x 3m What3Words Property Grid Coordinates & 3x3 (1m x 1m) Sub-Cell Slots
2. Property Parcel Borders, Building Footprints & Editable Land Zones
3. Botanical Species Catalog (Trees, Shrubs, Perennials, Vegetables/Herbs, Groundcovers)
4. Planted Items, Seasonal Care Logs & GCS Growth Timeline Photos
5. Garden Hose Reach, Root-Foundation Safety & Mature Canopy Analysis
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class PlantCategory(str, Enum):
    SHADE_TREE = "shade_tree"
    FRUIT_TREE = "fruit_tree"
    EVERGREEN_TREE = "evergreen_tree"
    FLOWERING_SHRUB = "flowering_shrub"
    PERENNIAL = "perennial"
    VEGETABLE_HERB = "vegetable_herb"
    GROUNDCOVER_VINE = "groundcover_vine"


class SubCellSlot(str, Enum):
    """3x3 (1m x 1m) sub-grid slots inside a single 3m x 3m What3Words square."""

    CENTER = "C"
    NW = "NW"
    N = "N"
    NE = "NE"
    W = "W"
    E = "E"
    SW = "SW"
    S = "S"
    SE = "SE"


class PlantHealthStatus(str, Enum):
    THRIVING = "thriving"
    BLOOMING = "blooming"
    FRUITING = "fruiting"
    NEEDS_WATER = "needs_water"
    NEEDS_PRUNING = "needs_pruning"
    DORMANT = "dormant"
    NEWLY_PLANTED = "newly_planted"


class SunRequirement(str, Enum):
    FULL_SUN = "full_sun"
    PART_SHADE = "part_shade"
    FULL_SHADE = "full_shade"


class RootAggression(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CareActionType(str, Enum):
    WATERED = "watered"
    PRUNED = "pruned"
    FERTILIZED = "fertilized"
    MULCHED = "mulched"
    HARVESTED = "harvested"
    OBSERVATION = "observation"


class LandCellType(str, Enum):
    YARD = "yard"
    HOUSE_ROOF = "house_roof"
    GARAGE_SHED = "garage_shed"
    DRIVEWAY_PATIO = "driveway_patio"
    FENCE_BORDER = "fence_border"
    ROAD_APRON = "road_apron"
    SEPTIC_UTILITY = "septic_utility"
    OUT_OF_BOUNDS = "out_of_bounds"


# ---------------------------------------------------------------------------
# 1. 3m x 3m Grid Coordinate & Parcel Border Models
# ---------------------------------------------------------------------------
class GridSquareCoord(BaseModel):
    row: int = Field(..., ge=0, le=60, description="0-indexed grid row from North to South")
    col: int = Field(..., ge=0, le=60, description="0-indexed grid col from West to East")
    label: Optional[str] = Field(default=None, description="Human-friendly cell code, e.g. 'R6-C18'")
    lat: float = Field(..., description="WGS84 latitude of the 3m x 3m square center")
    lng: float = Field(..., description="WGS84 longitude of the 3m x 3m square center")
    w3w: Optional[str] = Field(default=None, description="Resolved what3words address (///w1.w2.w3)")


class ParcelZoneMap(BaseModel):
    """Tracks the land classification of cells so homeowners can adjust borders, roofs, patios & fences."""

    house_roof_cells: List[str] = Field(default_factory=list, description="List of 'R{r}-C{c}' labels for main house")
    garage_shed_cells: List[str] = Field(default_factory=list, description="List of 'R{r}-C{c}' labels for garage/shed")
    driveway_patio_cells: List[str] = Field(default_factory=list, description="List of 'R{r}-C{c}' labels for hardscape")
    fence_border_cells: List[str] = Field(default_factory=list, description="List of 'R{r}-C{c}' labels for fence/property border")
    road_apron_cells: List[str] = Field(default_factory=list, description="List of 'R{r}-C{c}' labels for street apron")
    septic_utility_cells: List[str] = Field(default_factory=list, description="List of 'R{r}-C{c}' labels for septic/underground utilities")
    out_of_bounds_cells: List[str] = Field(default_factory=list, description="List of 'R{r}-C{c}' labels outside the property lot")
    border_polygon_lat_lng: List[List[float]] = Field(
        default_factory=list,
        description="Outer property boundary vertices [[lat, lng], ...] adjustable by the user",
    )
    footprint_source: str = Field(default="osm_overpass_and_parcel_fit")


# ---------------------------------------------------------------------------
# 2. Botanical Species Catalog Models
# ---------------------------------------------------------------------------
class SpeciesCatalogItem(BaseModel):
    species_id: str
    common_name: str
    scientific_name: str
    category: PlantCategory
    emoji: str = "🌳"
    min_usda_zone: int = 3
    max_usda_zone: int = 9
    sun_need: SunRequirement = SunRequirement.FULL_SUN
    water_interval_days: int = 7
    mature_spread_ft: float = 15.0
    mature_height_ft: float = 20.0
    root_aggression: RootAggression = RootAggression.LOW
    bloom_season: str = "Spring"
    care_summary: str = ""
    seasonal_colors: Dict[str, str] = Field(
        default_factory=lambda: {
            "spring": "#4ade80",
            "summer": "#16a34a",
            "autumn": "#ea580c",
            "winter": "#78716c",
        }
    )


# ---------------------------------------------------------------------------
# 3. Planted Item, Care Log & Scrapbook Photo Models
# ---------------------------------------------------------------------------
class CareLogEntry(BaseModel):
    log_id: str
    action: CareActionType = CareActionType.WATERED
    note: Optional[str] = None
    logged_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


class PlantPhoto(BaseModel):
    photo_id: str
    gcs_uri: str
    public_url: str
    caption: Optional[str] = None
    season_tag: str = "summer"
    uploaded_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


class PlantedItem(BaseModel):
    plant_id: str
    species_id: str
    common_name: str
    scientific_name: str = ""
    nickname: Optional[str] = None
    category: PlantCategory = PlantCategory.FRUIT_TREE
    emoji: str = "🌳"
    square: GridSquareCoord
    sub_slot: SubCellSlot = SubCellSlot.CENTER
    planted_date: str = Field(default_factory=lambda: datetime.utcnow().strftime("%Y-%m-%d"))
    age_years: float = 1.0
    health_status: PlantHealthStatus = PlantHealthStatus.THRIVING
    sun_need: SunRequirement = SunRequirement.FULL_SUN
    water_interval_days: int = 7
    last_watered_at: Optional[str] = None
    mature_spread_ft: float = 15.0
    mature_height_ft: float = 20.0
    root_aggression: RootAggression = RootAggression.LOW
    seasonal_colors: Dict[str, str] = Field(
        default_factory=lambda: {
            "spring": "#4ade80",
            "summer": "#16a34a",
            "autumn": "#ea580c",
            "winter": "#78716c",
        }
    )
    notes: Optional[str] = None
    care_logs: List[CareLogEntry] = Field(default_factory=list)
    photos: List[PlantPhoto] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 4. Yard Spatial Analysis: Hose Reach, Root Hazards & Canopy Report
# ---------------------------------------------------------------------------
class YardAnalysisRequest(BaseModel):
    parcel_center_lat: float = 44.2601
    parcel_center_lng: float = -72.5754
    usda_zone: str = "5a"
    hose_length_ft: int = 100
    spigot_s1: Optional[GridSquareCoord] = None
    spigot_s2: Optional[GridSquareCoord] = None
    hose_waypoints: List[GridSquareCoord] = Field(default_factory=list)
    zones: ParcelZoneMap = Field(default_factory=ParcelZoneMap)
    plants: List[PlantedItem] = Field(default_factory=list)


class PlantSpatialCheck(BaseModel):
    plant_id: str
    common_name: str
    cell_label: str
    w3w: Optional[str] = None
    nearest_spigot_label: str = "S1"
    hose_pull_required_ft: float = 0.0
    hose_reachable: bool = True
    sun_exposure_in_cell: SunRequirement = SunRequirement.FULL_SUN
    sun_compatible: bool = True
    usda_zone_compatible: bool = True
    root_foundation_conflict: bool = False
    root_septic_conflict: bool = False
    canopy_border_overhang: bool = False
    warnings: List[str] = Field(default_factory=list)


class YardSafetyAndHoseReport(BaseModel):
    total_plants: int = 0
    trees_count: int = 0
    shrubs_perennials_count: int = 0
    garden_beds_count: int = 0
    hose_length_ft: int = 100
    max_hose_pull_ft: float = 0.0
    all_plants_within_hose_reach: bool = True
    root_conflict_count: int = 0
    canopy_overhang_count: int = 0
    sun_mismatch_count: int = 0
    plants_needing_attention: int = 0
    plant_checks: List[PlantSpatialCheck] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 5. Persisted Residential Yard Document (`{prefix}yards` in Firestore)
# ---------------------------------------------------------------------------
class YardMap(BaseModel):
    yard_id: str
    yard_name: str = "My Home Botanical Yard"
    owner_name: str = "Homeowner"
    street_address: str
    parcel_center_lat: float
    parcel_center_lng: float
    usda_zone: str = "5b"
    usda_min_temp_f: str = "-15°F to -10°F"
    grid_rows: int = 18
    grid_cols: int = 26
    square_size_meters: float = 3.0
    square_size_feet: float = 9.84
    hose_length_ft: int = 100
    spigot_s1: GridSquareCoord
    spigot_s2: Optional[GridSquareCoord] = None
    hose_waypoints: List[GridSquareCoord] = Field(default_factory=list)
    zones: ParcelZoneMap = Field(default_factory=ParcelZoneMap)
    plants: List[PlantedItem] = Field(default_factory=list)
    analysis: YardSafetyAndHoseReport = Field(default_factory=YardSafetyAndHoseReport)
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


class PlantCreateRequest(BaseModel):
    species_id: str
    nickname: Optional[str] = None
    row: int
    col: int
    sub_slot: SubCellSlot = SubCellSlot.CENTER
    planted_date: Optional[str] = None
    age_years: float = 1.0
    health_status: PlantHealthStatus = PlantHealthStatus.THRIVING
    notes: Optional[str] = None


class CareLogCreateRequest(BaseModel):
    action: CareActionType = CareActionType.WATERED
    note: Optional[str] = None
    health_status: Optional[PlantHealthStatus] = None
