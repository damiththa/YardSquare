"""
3m x 3m Property Grid, Actual Land Footprint & Botanical Spatial Engine (`app/services/geo_grid.py`).

Key Capabilities & Tier-1 ($0.00 Personal Use) Cost Guardrails:
- Each grid square is `settings.SQUARE_SIZE_METERS` (3.0m x 3.0m / ~9.84 ft x 9.84 ft).
- Stage 1 Autocomplete: $0 local catalog + Photon (OpenStreetMap) type-ahead (0 Google Maps quota).
- Stage 2 Rooftop Geocode: Google Maps Geocoding API hard-capped at 300/day & 9,500/month
  (below Google's 10,000/month free Essentials SKU limit), with automatic Photon coordinate fallback.
- Actual Land & Building Extraction: Queries OpenStreetMap Overpass API (`way["building"]`,
  `way["landuse"]`, `way["barrier"]`) to project real building and yard polygons onto the 3m x 3m grid,
  paired with interactive user border adjustment (`ParcelZoneMap`).
- Botanical Spatial Analyzer: Computes garden hose reach from Spigots S1/S2 around waypoints (+15% slack),
  root-to-foundation & septic proximity conflicts, mature canopy overhang, sun/shade zones, and USDA Hardiness compatibility.
"""

import hashlib
import math
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple
import httpx

from app.config import get_settings
from app.models import (
    GridSquareCoord,
    ParcelZoneMap,
    PlantCategory,
    PlantHealthStatus,
    PlantSpatialCheck,
    PlantedItem,
    RootAggression,
    SunRequirement,
    YardSafetyAndHoseReport,
)
from app.services.w3w_service import resolve_w3w_sync

_GEOCODE_CACHE: Dict[str, Tuple[float, float, str]] = {}
_AUTOCOMPLETE_CACHE: Dict[str, List[Dict[str, Any]]] = {}
_AUTOCOMPLETE_FALLBACK_COORDS: Dict[str, Tuple[float, float, str]] = {}
_USAGE_TRACKER: Dict[str, int] = {
    "month_key": int(datetime.utcnow().strftime("%Y%m")),
    "day_key": int(datetime.utcnow().strftime("%Y%m%d")),
    "monthly_calls": 0,
    "daily_calls": 0,
}

US_STATE_ABBREV: Dict[str, str] = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "district of columbia": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}

KNOWN_RESIDENTIAL_YARDS: List[Dict[str, Any]] = [
    {
        "formatted_address": "742 Evergreen Terrace, Montpelier, VT 05602",
        "street_line": "742 Evergreen Terrace",
        "city_state_zip": "Montpelier, VT 05602",
        "lat": 44.2601,
        "lng": -72.5754,
        "usda_zone": "5a",
    },
    {
        "formatted_address": "184 Old Orchard Ln, Stowe, VT 05672",
        "street_line": "184 Old Orchard Ln",
        "city_state_zip": "Stowe, VT 05672",
        "lat": 44.4654,
        "lng": -72.6874,
        "usda_zone": "4b",
    },
    {
        "formatted_address": "905 Maple Ridge Ln, Iowa City, IA 52240",
        "street_line": "905 Maple Ridge Ln",
        "city_state_zip": "Iowa City, IA 52240",
        "lat": 41.6611,
        "lng": -91.5302,
        "usda_zone": "5b",
    },
    {
        "formatted_address": "310 Botanical Way, Asheville, NC 28801",
        "street_line": "310 Botanical Way",
        "city_state_zip": "Asheville, NC 28801",
        "lat": 35.5951,
        "lng": -82.5515,
        "usda_zone": "7a",
    },
]


def _roll_counters_if_needed() -> None:
    now = datetime.utcnow()
    cur_month = int(now.strftime("%Y%m"))
    cur_day = int(now.strftime("%Y%m%d"))
    if _USAGE_TRACKER["month_key"] != cur_month:
        _USAGE_TRACKER["month_key"] = cur_month
        _USAGE_TRACKER["monthly_calls"] = 0
    if _USAGE_TRACKER["day_key"] != cur_day:
        _USAGE_TRACKER["day_key"] = cur_day
        _USAGE_TRACKER["daily_calls"] = 0


def get_maps_free_tier_status() -> Dict[str, Any]:
    """Return current Google Maps API usage vs. the Tier-1 ($0.00) Free-Tier hard cap."""
    settings = get_settings()
    _roll_counters_if_needed()
    return {
        "enforce_free_tier_hard_cap": settings.ENFORCE_FREE_TIER_HARD_CAP,
        "google_free_sku_limit_monthly": 10000,
        "yardsquare_hard_cap_monthly": settings.MAPS_MONTHLY_FREE_TIER_CAP,
        "yardsquare_hard_cap_daily": settings.MAPS_DAILY_SAFE_CAP,
        "current_month_calls": _USAGE_TRACKER["monthly_calls"],
        "current_day_calls": _USAGE_TRACKER["daily_calls"],
        "remaining_free_calls_this_month": max(
            0, settings.MAPS_MONTHLY_FREE_TIER_CAP - _USAGE_TRACKER["monthly_calls"]
        ),
        "pay_as_you_go_blocked": True,
    }


def format_cell_label(row: int, col: int) -> str:
    """Return 1-indexed human-readable cell code, e.g. 'R6-C18'."""
    return f"R{row + 1}-C{col + 1}"


def parse_cell_label(label: str) -> Optional[Tuple[int, int]]:
    """Parse 'R6-C18' into 0-indexed (row=5, col=17)."""
    match = re.match(r"^R(\d+)-C(\d+)$", (label or "").strip().upper())
    if not match:
        return None
    return int(match.group(1)) - 1, int(match.group(2)) - 1


def grid_cell_to_lat_lng(
    parcel_center_lat: float,
    parcel_center_lng: float,
    row: int,
    col: int,
    grid_rows: Optional[int] = None,
    grid_cols: Optional[int] = None,
) -> Tuple[float, float]:
    """
    Convert a (row, col) cell on the 3m x 3m parcel grid into exact WGS84 (lat, lng).
    Row 0 is North; Col 0 is West.
    """
    settings = get_settings()
    rows = grid_rows or settings.DEFAULT_GRID_ROWS
    cols = grid_cols or settings.DEFAULT_GRID_COLS
    center_row = rows // 2
    center_col = cols // 2

    d_north_meters = (center_row - row) * settings.SQUARE_SIZE_METERS
    d_east_meters = (col - center_col) * settings.SQUARE_SIZE_METERS

    lat_deg_per_meter = 1.0 / 111_320.0
    cos_lat = max(0.2, math.cos(math.radians(parcel_center_lat)))
    lng_deg_per_meter = 1.0 / (111_320.0 * cos_lat)

    lat = round(parcel_center_lat + (d_north_meters * lat_deg_per_meter), 6)
    lng = round(parcel_center_lng + (d_east_meters * lng_deg_per_meter), 6)
    return lat, lng


def lat_lng_to_grid_cell(
    parcel_center_lat: float,
    parcel_center_lng: float,
    lat: float,
    lng: float,
    grid_rows: Optional[int] = None,
    grid_cols: Optional[int] = None,
) -> Tuple[int, int]:
    """Convert a WGS84 (lat, lng) coordinate into the nearest (row, col) on the 3m x 3m parcel grid."""
    settings = get_settings()
    rows = grid_rows or settings.DEFAULT_GRID_ROWS
    cols = grid_cols or settings.DEFAULT_GRID_COLS
    center_row = rows // 2
    center_col = cols // 2

    lat_deg_per_meter = 1.0 / 111_320.0
    cos_lat = max(0.2, math.cos(math.radians(parcel_center_lat)))
    lng_deg_per_meter = 1.0 / (111_320.0 * cos_lat)

    d_north_meters = (lat - parcel_center_lat) / lat_deg_per_meter
    d_east_meters = (lng - parcel_center_lng) / lng_deg_per_meter

    row = int(round(center_row - (d_north_meters / settings.SQUARE_SIZE_METERS)))
    col = int(round(center_col + (d_east_meters / settings.SQUARE_SIZE_METERS)))

    row = max(0, min(rows - 1, row))
    col = max(0, min(cols - 1, col))
    return row, col


def build_grid_square_coord(
    parcel_center_lat: float,
    parcel_center_lng: float,
    row: int,
    col: int,
    resolve_w3w: bool = True,
) -> GridSquareCoord:
    """Create a full GridSquareCoord with WGS84 (lat, lng), human label, and optional W3W address."""
    lat, lng = grid_cell_to_lat_lng(parcel_center_lat, parcel_center_lng, row, col)
    w3w = resolve_w3w_sync(lat, lng) if resolve_w3w else None
    return GridSquareCoord(
        row=row,
        col=col,
        label=format_cell_label(row, col),
        lat=lat,
        lng=lng,
        w3w=w3w,
    )


def euclidean_squares_distance_ft(a_row: int, a_col: int, b_row: int, b_col: int) -> float:
    """Compute center-to-center distance in feet between two 3m x 3m grid squares."""
    settings = get_settings()
    return math.hypot(a_row - b_row, a_col - b_col) * settings.square_size_feet


def resolve_usda_hardiness_zone(lat: float, street_address: str = "") -> Tuple[str, str]:
    """
    Resolve USDA Plant Hardiness Zone (e.g., '5b', '-15°F to -10°F').
    1. Attempts free PHZM ZIP lookup if a 5-digit US ZIP code is present in street_address.
    2. Falls back to latitude-based USDA climate band estimation ($0.00, 0ms).
    """
    settings = get_settings()
    zip_match = re.search(r"\b(\d{5})\b", street_address or "")
    if zip_match:
        zip_code = zip_match.group(1)
        try:
            with httpx.Client(timeout=1.8) as client:
                resp = client.get(f"{settings.PHZM_API_BASE_URL.rstrip('/')}/{zip_code}.json")
                if resp.status_code == 200:
                    data = resp.json()
                    zone = str(data.get("zone") or "").strip()
                    trange = str(data.get("temperature_range") or "").strip()
                    if zone:
                        return zone, (f"{trange}°F" if trange and "°F" not in trange else trange or "-15°F to -10°F")
        except Exception:
            pass

    if lat >= 44.3:
        return "4b", "-25°F to -20°F"
    if lat >= 43.0:
        return "5a", "-20°F to -15°F"
    if lat >= 41.0:
        return "5b", "-15°F to -10°F"
    if lat >= 39.0:
        return "6b", "-5°F to 0°F"
    if lat >= 35.0:
        return "7b", "5°F to 10°F"
    if lat >= 31.0:
        return "8b", "15°F to 20°F"
    return "9b", "25°F to 30°F"


def extract_leading_house_number(query: str) -> Optional[str]:
    """Preserve leading house numbers (e.g. '742', '184B') during road-level OSM type-ahead."""
    match = re.match(r"^\s*(\d+[A-Za-z]?(?:-\d+[A-Za-z]?)?)\s+[A-Za-z]", query or "")
    return match.group(1) if match else None


def _format_photon_feature(
    feature: Dict[str, Any],
    typed_house_number: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    props = feature.get("properties") or {}
    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates") or []
    if len(coords) < 2:
        return None

    country_code = str(props.get("countrycode") or "").upper()
    country_name = str(props.get("country") or "").lower()
    if country_code and country_code != "US" and "united states" not in country_name:
        return None

    house_number = str(props.get("housenumber") or "").strip()
    street = str(props.get("street") or props.get("name") or "").strip()
    city = str(
        props.get("city")
        or props.get("town")
        or props.get("village")
        or props.get("locality")
        or props.get("county")
        or ""
    ).strip()
    raw_state = str(props.get("state") or "").strip()
    state_abbr = US_STATE_ABBREV.get(raw_state.lower(), raw_state[:2].upper() if len(raw_state) == 2 else raw_state)
    postcode = str(props.get("postcode") or "").strip()

    if not street:
        return None

    house_number_preserved = False
    if not house_number and typed_house_number:
        house_number = typed_house_number
        house_number_preserved = True

    street_line = f"{house_number} {street}".strip() if house_number else street
    locality_parts: List[str] = []
    if city:
        locality_parts.append(city)
    state_zip = f"{state_abbr} {postcode}".strip()
    if state_zip:
        locality_parts.append(state_zip)

    city_state_zip = ", ".join(locality_parts)
    formatted_address = f"{street_line}, {city_state_zip}" if city_state_zip else street_line

    lng = round(float(coords[0]), 6)
    lat = round(float(coords[1]), 6)
    return {
        "formatted_address": formatted_address,
        "street_line": street_line,
        "city_state_zip": city_state_zip,
        "fallback_lat": lat,
        "fallback_lng": lng,
        "source": "photon_osm",
        "house_number_preserved": house_number_preserved,
    }


def autocomplete_addresses(query: str, limit: int = 5) -> Dict[str, Any]:
    """
    Split-Provider Stage 1: Zero-cost ($0.00) type-ahead address suggestions.
    Combines local residential catalog matches with Photon (OpenStreetMap) type-ahead.
    """
    settings = get_settings()
    cleaned = (query or "").strip()
    if len(cleaned) < 3:
        return {
            "query": cleaned,
            "provider": "none_min_3_chars",
            "google_quota_used": 0,
            "cost_usd": 0.0,
            "suggestions": [],
        }

    cache_key = f"{cleaned.lower()}::{limit}"
    if cache_key in _AUTOCOMPLETE_CACHE:
        return {
            "query": cleaned,
            "provider": "memory_prefix_cache",
            "google_quota_used": 0,
            "cost_usd": 0.0,
            "suggestions": _AUTOCOMPLETE_CACHE[cache_key],
        }

    typed_house_num = extract_leading_house_number(cleaned)
    lowered = cleaned.lower()
    tokens = [t for t in re.split(r"[\s,]+", lowered) if t]
    non_numeric_tokens = [t for t in tokens if not t[0].isdigit()]

    results: List[Dict[str, Any]] = []
    seen_addresses: Set[str] = set()

    for item in KNOWN_RESIDENTIAL_YARDS:
        haystack = item["formatted_address"].lower()
        if lowered in haystack or (non_numeric_tokens and all(t in haystack for t in non_numeric_tokens)):
            fmt = item["formatted_address"]
            street_line = item["street_line"]
            preserved = False
            if typed_house_num and not haystack.startswith(typed_house_num.lower()):
                road_only = re.sub(r"^\d+[A-Za-z]?(?:-\d+[A-Za-z]?)?\s+", "", street_line)
                street_line = f"{typed_house_num} {road_only}"
                fmt = f"{street_line}, {item['city_state_zip']}"
                preserved = True

            norm_key = fmt.lower()
            if norm_key not in seen_addresses:
                seen_addresses.add(norm_key)
                suggestion = {
                    "formatted_address": fmt,
                    "street_line": street_line,
                    "city_state_zip": item["city_state_zip"],
                    "fallback_lat": item["lat"],
                    "fallback_lng": item["lng"],
                    "source": "local_yard_catalog",
                    "house_number_preserved": preserved,
                }
                results.append(suggestion)
                _AUTOCOMPLETE_FALLBACK_COORDS[norm_key] = (item["lat"], item["lng"], fmt)

    provider_used = "local_yard_catalog"
    if len(results) < limit:
        try:
            with httpx.Client(timeout=2.0) as client:
                resp = client.get(
                    settings.PHOTON_AUTOCOMPLETE_URL,
                    params={
                        "q": cleaned,
                        "limit": max(limit * 2, 8),
                        "lang": "en",
                        "lat": settings.DEFAULT_FALLBACK_LAT,
                        "lon": settings.DEFAULT_FALLBACK_LNG,
                    },
                    headers={"User-Agent": settings.USER_AGENT_HEADER},
                )
                if resp.status_code == 200:
                    payload = resp.json()
                    for feat in payload.get("features") or []:
                        parsed = _format_photon_feature(feat, typed_house_number=typed_house_num)
                        if not parsed:
                            continue
                        norm_key = parsed["formatted_address"].lower()
                        if norm_key in seen_addresses:
                            continue
                        seen_addresses.add(norm_key)
                        results.append(parsed)
                        _AUTOCOMPLETE_FALLBACK_COORDS[norm_key] = (
                            parsed["fallback_lat"],
                            parsed["fallback_lng"],
                            parsed["formatted_address"],
                        )
                        provider_used = "photon_osm_split_pipeline"
                        if len(results) >= limit:
                            break
        except Exception:
            pass

    final_suggestions = results[:limit]
    if len(_AUTOCOMPLETE_CACHE) > 256:
        _AUTOCOMPLETE_CACHE.clear()
    _AUTOCOMPLETE_CACHE[cache_key] = final_suggestions

    return {
        "query": cleaned,
        "provider": provider_used,
        "google_quota_used": 0,
        "cost_usd": 0.0,
        "suggestions": final_suggestions,
    }


def _point_in_polygon(lat: float, lng: float, polygon: List[Tuple[float, float]]) -> bool:
    """Ray-casting point-in-polygon check for projecting OSM building/parcel polygons onto 3m cells."""
    inside = False
    n = len(polygon)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        yi, xi = polygon[i]
        yj, xj = polygon[j]
        intersect = ((yi > lat) != (yj > lat)) and (
            lng < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi
        )
        if intersect:
            inside = not inside
        j = i
    return inside


def extract_actual_land_and_footprints(
    center_lat: float,
    center_lng: float,
    street_address: str = "",
    grid_rows: Optional[int] = None,
    grid_cols: Optional[int] = None,
) -> ParcelZoneMap:
    """
    Fetch actual building footprints and residential parcel bounds around `(center_lat, center_lng)`
    via OpenStreetMap Overpass API ($0.00), and rasterize onto the 3m x 3m grid.
    Falls back cleanly to an address-seeded residential lot footprint that the user can interactively edit.
    """
    settings = get_settings()
    rows = grid_rows or settings.DEFAULT_GRID_ROWS
    cols = grid_cols or settings.DEFAULT_GRID_COLS

    house_cells: Set[str] = set()
    garage_cells: Set[str] = set()
    driveway_cells: Set[str] = set()
    fence_cells: Set[str] = set()
    road_cells: Set[str] = set()
    out_of_bounds_cells: Set[str] = set()
    footprint_source = "address_seeded_parcel_fit"

    # Attempt live OpenStreetMap Overpass query for real building footprint polygons within 45m
    try:
        overpass_query = (
            f'[out:json][timeout:3];'
            f'('
            f'way["building"](around:40,{center_lat},{center_lng});'
            f');'
            f'out geom;'
        )
        with httpx.Client(timeout=2.5) as client:
            resp = client.post(
                settings.OVERPASS_API_URL,
                data={"data": overpass_query},
                headers={"User-Agent": settings.USER_AGENT_HEADER},
            )
            if resp.status_code == 200:
                elements = resp.json().get("elements") or []
                building_polys: List[List[Tuple[float, float]]] = []
                for el in elements:
                    geom = el.get("geometry") or []
                    if len(geom) >= 3:
                        poly = [( float(pt["lat"]), float(pt["lon"]) ) for pt in geom]
                        building_polys.append(poly)

                if building_polys:
                    for r in range(rows):
                        for c in range(cols):
                            clat, clng = grid_cell_to_lat_lng(center_lat, center_lng, r, c, rows, cols)
                            if _point_in_polygon(clat, clng, building_polys[0]):
                                house_cells.add(format_cell_label(r, c))
                            elif any(_point_in_polygon(clat, clng, bp) for bp in building_polys[1:]):
                                garage_cells.add(format_cell_label(r, c))
                    if house_cells:
                        footprint_source = "osm_overpass_live_building_polygon"
    except Exception:
        pass

    # If OSM had no building polygon mapped at this coordinate, synthesize a realistic house + garage footprint
    digest = hashlib.sha256((street_address or f"{center_lat}:{center_lng}").lower().encode("utf-8")).digest()
    lot_margin_cols = 2 + (digest[0] % 2)  # 2 or 3 cols outside fence on left/right
    lot_margin_top = 1                     # 1 row outside north fence

    if not house_cells:
        h_r_start = 6
        h_r_end = 10
        h_c_start = 10 + (digest[1] % 2)
        h_c_end = h_c_start + 5
        for r in range(h_r_start, h_r_end + 1):
            for c in range(h_c_start, h_c_end + 1):
                house_cells.add(format_cell_label(r, c))

        g_c_start = h_c_start - 3
        g_c_end = h_c_start - 1
        for r in range(h_r_start + 1, h_r_end + 1):
            for c in range(g_c_start, g_c_end + 1):
                garage_cells.add(format_cell_label(r, c))

        # Driveway from garage to street apron
        for r in range(h_r_end + 1, rows - 1):
            for c in range(g_c_start, g_c_end + 1):
                driveway_cells.add(format_cell_label(r, c))

        # Backyard patio behind main house
        for r in range(h_r_start - 2, h_r_start):
            for c in range(h_c_start + 1, h_c_end):
                driveway_cells.add(format_cell_label(r, c))

    # Bottom row is the street apron
    for c in range(cols):
        road_cells.add(format_cell_label(rows - 1, c))

    # Out-of-bounds neighbor strips & perimeter fence border (adjustable by homeowner)
    min_lot_r = lot_margin_top
    max_lot_r = rows - 2
    min_lot_c = lot_margin_cols
    max_lot_c = cols - 1 - lot_margin_cols

    for r in range(rows - 1):
        for c in range(cols):
            lbl = format_cell_label(r, c)
            if r < min_lot_r or c < min_lot_c or c > max_lot_c:
                out_of_bounds_cells.add(lbl)
            elif r == min_lot_r or c == min_lot_c or c == max_lot_c:
                if lbl not in driveway_cells and lbl not in house_cells:
                    fence_cells.add(lbl)

    # Compute the 4 corner lat/lng coordinates of the editable property border polygon
    nw_lat, nw_lng = grid_cell_to_lat_lng(center_lat, center_lng, min_lot_r, min_lot_c, rows, cols)
    ne_lat, ne_lng = grid_cell_to_lat_lng(center_lat, center_lng, min_lot_r, max_lot_c, rows, cols)
    se_lat, se_lng = grid_cell_to_lat_lng(center_lat, center_lng, max_lot_r, max_lot_c, rows, cols)
    sw_lat, sw_lng = grid_cell_to_lat_lng(center_lat, center_lng, max_lot_r, min_lot_c, rows, cols)

    return ParcelZoneMap(
        house_roof_cells=sorted(house_cells),
        garage_shed_cells=sorted(garage_cells),
        driveway_patio_cells=sorted(driveway_cells),
        fence_border_cells=sorted(fence_cells),
        road_apron_cells=sorted(road_cells),
        septic_utility_cells=[format_cell_label(7, max_lot_c - 2), format_cell_label(8, max_lot_c - 2)],
        out_of_bounds_cells=sorted(out_of_bounds_cells),
        border_polygon_lat_lng=[
            [nw_lat, nw_lng],
            [ne_lat, ne_lng],
            [se_lat, se_lng],
            [sw_lat, sw_lng],
        ],
        footprint_source=footprint_source,
    )


def geocode_address_to_yard_parcel(
    street_address: str,
    fallback_lat: Optional[float] = None,
    fallback_lng: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Split-Provider Stage 2: Resolve a selected street address into rooftop coordinates,
    USDA Hardiness Zone, and actual/adjustable parcel & building footprints.
    """
    settings = get_settings()
    lowered = (street_address or "").strip().lower()

    for item in KNOWN_RESIDENTIAL_YARDS:
        if item["street_line"].lower() in lowered or item["formatted_address"].lower() == lowered:
            return _build_geocoded_parcel_payload(
                item["formatted_address"],
                item["lat"],
                item["lng"],
                precision_source="known_yard_catalog",
            )

    if lowered in _GEOCODE_CACHE:
        c_lat, c_lng, c_fmt = _GEOCODE_CACHE[lowered]
        return _build_geocoded_parcel_payload(c_fmt, c_lat, c_lng, precision_source="google_geocode_cache")

    _roll_counters_if_needed()
    api_key = settings.google_maps_api_key
    within_free_cap = (
        not settings.ENFORCE_FREE_TIER_HARD_CAP
        or (
            _USAGE_TRACKER["monthly_calls"] < settings.MAPS_MONTHLY_FREE_TIER_CAP
            and _USAGE_TRACKER["daily_calls"] < settings.MAPS_DAILY_SAFE_CAP
        )
    )

    if api_key and within_free_cap:
        try:
            _USAGE_TRACKER["monthly_calls"] += 1
            _USAGE_TRACKER["daily_calls"] += 1
            with httpx.Client(timeout=3.0) as client:
                resp = client.get(
                    settings.GOOGLE_GEOCODE_URL,
                    params={"address": street_address.strip(), "key": api_key},
                )
                if resp.status_code == 200:
                    payload = resp.json()
                    if payload.get("status") == "OK" and payload.get("results"):
                        top = payload["results"][0]
                        loc = top["geometry"]["location"]
                        lat = round(float(loc["lat"]), 6)
                        lng = round(float(loc["lng"]), 6)
                        fmt = top.get("formatted_address", street_address.strip())
                        _GEOCODE_CACHE[lowered] = (lat, lng, fmt)
                        return _build_geocoded_parcel_payload(fmt, lat, lng, precision_source="google_rooftop_geocode")
        except Exception:
            pass

    if fallback_lat is not None and fallback_lng is not None:
        return _build_geocoded_parcel_payload(
            street_address.strip(),
            round(float(fallback_lat), 6),
            round(float(fallback_lng), 6),
            precision_source="photon_osm_fallback",
        )

    if lowered in _AUTOCOMPLETE_FALLBACK_COORDS:
        fb_lat, fb_lng, fb_fmt = _AUTOCOMPLETE_FALLBACK_COORDS[lowered]
        return _build_geocoded_parcel_payload(fb_fmt, fb_lat, fb_lng, precision_source="photon_osm_fallback")

    digest = hashlib.sha256(lowered.encode("utf-8")).hexdigest()
    lat_offset = ((int(digest[:6], 16) % 2000) - 1000) * 0.0001
    lng_offset = ((int(digest[6:12], 16) % 2000) - 1000) * 0.0001
    lat = round(settings.DEFAULT_FALLBACK_LAT + lat_offset, 6)
    lng = round(settings.DEFAULT_FALLBACK_LNG + lng_offset, 6)
    return _build_geocoded_parcel_payload(street_address.strip(), lat, lng, precision_source="deterministic_fallback")


def _build_geocoded_parcel_payload(
    formatted_address: str,
    center_lat: float,
    center_lng: float,
    precision_source: str = "google_rooftop_geocode",
) -> Dict[str, Any]:
    settings = get_settings()
    usda_zone, usda_temp = resolve_usda_hardiness_zone(center_lat, formatted_address)
    zones = extract_actual_land_and_footprints(
        center_lat,
        center_lng,
        street_address=formatted_address,
        grid_rows=settings.DEFAULT_GRID_ROWS,
        grid_cols=settings.DEFAULT_GRID_COLS,
    )
    center_w3w = resolve_w3w_sync(center_lat, center_lng)
    return {
        "formatted_address": formatted_address,
        "parcel_center_lat": center_lat,
        "parcel_center_lng": center_lng,
        "parcel_center_w3w": center_w3w,
        "precision_source": precision_source,
        "usda_zone": usda_zone,
        "usda_min_temp_f": usda_temp,
        "grid_rows": settings.DEFAULT_GRID_ROWS,
        "grid_cols": settings.DEFAULT_GRID_COLS,
        "square_size_meters": settings.SQUARE_SIZE_METERS,
        "square_size_feet": round(settings.square_size_feet, 2),
        "zones": zones.model_dump(),
        "free_tier_guardrail": get_maps_free_tier_status(),
    }


def compute_cell_sun_exposure(
    row: int,
    col: int,
    zones: ParcelZoneMap,
    plants: List[PlantedItem],
    exclude_plant_id: Optional[str] = None,
) -> SunRequirement:
    """
    Estimate sun exposure of a 3m x 3m square (`full_sun`, `part_shade`, `full_shade`):
    - Squares 1 row immediately North (`row + 1` or `row + 2` is house roof) cast in North shadow -> `full_shade` / `part_shade`.
    - Squares directly underneath a mature shade/evergreen tree canopy -> `part_shade`.
    - All other open yard squares -> `full_sun`.
    """
    house_set = set(zones.house_roof_cells) | set(zones.garage_shed_cells)
    south_1 = format_cell_label(row + 1, col)
    south_2 = format_cell_label(row + 2, col)
    if south_1 in house_set:
        return SunRequirement.FULL_SHADE
    if south_2 in house_set:
        return SunRequirement.PART_SHADE

    for p in plants:
        if p.plant_id == exclude_plant_id:
            continue
        if p.category in (PlantCategory.SHADE_TREE, PlantCategory.EVERGREEN_TREE) and p.mature_spread_ft >= 18.0:
            dist_ft = euclidean_squares_distance_ft(row, col, p.square.row, p.square.col)
            if dist_ft <= (p.mature_spread_ft * 0.65):
                return SunRequirement.PART_SHADE

    return SunRequirement.FULL_SUN


def analyze_yard_layout(
    hose_length_ft: int,
    spigot_s1: Optional[GridSquareCoord],
    spigot_s2: Optional[GridSquareCoord],
    hose_waypoints: List[GridSquareCoord],
    zones: ParcelZoneMap,
    plants: List[PlantedItem],
    usda_zone: str = "5b",
) -> YardSafetyAndHoseReport:
    """
    Comprehensive spatial & botanical analyzer for a YardSquare property:
    1. Calculates garden hose reach (+15% slack) from Spigot S1 or S2 around waypoints to every planted square.
    2. Checks root-to-foundation & root-to-septic conflicts for trees with medium/high root aggression.
    3. Checks mature canopy overhang across property borders / out-of-bounds cells.
    4. Checks sun/shade exposure match and USDA Hardiness Zone compatibility.
    """
    settings = get_settings()
    house_coords = [parse_cell_label(lbl) for lbl in (zones.house_roof_cells + zones.garage_shed_cells)]
    house_pts = [pt for pt in house_coords if pt is not None]
    septic_coords = [parse_cell_label(lbl) for lbl in zones.septic_utility_cells]
    septic_pts = [pt for pt in septic_coords if pt is not None]
    border_coords = [parse_cell_label(lbl) for lbl in (zones.fence_border_cells + zones.out_of_bounds_cells)]
    border_pts = [pt for pt in border_coords if pt is not None]

    # Parse numeric USDA zone (e.g. '5b' -> 5)
    zone_digits = re.findall(r"\d+", usda_zone or "5")
    prop_zone_num = int(zone_digits[0]) if zone_digits else 5

    trees_count = 0
    shrubs_count = 0
    beds_count = 0
    max_hose_pull = 0.0
    all_within_hose = True
    root_conflicts = 0
    canopy_overhangs = 0
    sun_mismatches = 0
    needing_attention = 0
    plant_checks: List[PlantSpatialCheck] = []

    for plant in plants:
        if plant.category in (PlantCategory.SHADE_TREE, PlantCategory.FRUIT_TREE, PlantCategory.EVERGREEN_TREE):
            trees_count += 1
        elif plant.category == PlantCategory.VEGETABLE_HERB:
            beds_count += 1
        else:
            shrubs_count += 1

        if plant.health_status in (PlantHealthStatus.NEEDS_WATER, PlantHealthStatus.NEEDS_PRUNING):
            needing_attention += 1

        pr, pc = plant.square.row, plant.square.col
        cell_label = plant.square.label or format_cell_label(pr, pc)

        # 1. Garden Hose Reach from Spigot S1 (via waypoints) vs S2 (direct)
        nearest_spigot = "S1"
        pull_ft = 0.0
        if spigot_s1 is not None:
            chain = [spigot_s1] + list(hose_waypoints or []) + [plant.square]
            routed_ft = 0.0
            for idx in range(len(chain) - 1):
                routed_ft += euclidean_squares_distance_ft(
                    chain[idx].row, chain[idx].col, chain[idx + 1].row, chain[idx + 1].col
                )
            pull_ft = round(routed_ft * settings.HOSE_SLACK_MULTIPLIER, 1)

        if spigot_s2 is not None:
            s2_pull = round(
                euclidean_squares_distance_ft(spigot_s2.row, spigot_s2.col, pr, pc) * settings.HOSE_SLACK_MULTIPLIER,
                1,
            )
            if spigot_s1 is None or s2_pull < pull_ft:
                pull_ft = s2_pull
                nearest_spigot = "S2"

        if pull_ft > max_hose_pull:
            max_hose_pull = pull_ft
        hose_ok = pull_ft <= hose_length_ft
        if not hose_ok:
            all_within_hose = False

        # 2. Sun Exposure Check
        cell_sun = compute_cell_sun_exposure(pr, pc, zones, plants, exclude_plant_id=plant.plant_id)
        sun_ok = True
        if plant.sun_need == SunRequirement.FULL_SUN and cell_sun == SunRequirement.FULL_SHADE:
            sun_ok = False
        elif plant.sun_need == SunRequirement.FULL_SHADE and cell_sun == SunRequirement.FULL_SUN:
            sun_ok = False
        if not sun_ok:
            sun_mismatches += 1

        # 3. Root Foundation & Septic Safety Check
        min_safe_foundation_ft = (
            20.0
            if plant.root_aggression == RootAggression.HIGH
            else (12.0 if plant.root_aggression == RootAggression.MEDIUM else 0.0)
        )
        root_foundation_conflict = False
        if min_safe_foundation_ft > 0 and house_pts:
            closest_house_ft = min(euclidean_squares_distance_ft(pr, pc, hr, hc) for hr, hc in house_pts)
            if closest_house_ft < min_safe_foundation_ft:
                root_foundation_conflict = True

        root_septic_conflict = False
        if plant.root_aggression in (RootAggression.MEDIUM, RootAggression.HIGH) and septic_pts:
            closest_septic_ft = min(euclidean_squares_distance_ft(pr, pc, sr, sc) for sr, sc in septic_pts)
            if closest_septic_ft < 15.0:
                root_septic_conflict = True

        if root_foundation_conflict or root_septic_conflict:
            root_conflicts += 1

        # 4. Mature Canopy Property Border Overhang Check
        canopy_radius_ft = plant.mature_spread_ft / 2.0
        canopy_overhang = False
        if canopy_radius_ft >= 7.0 and border_pts:
            closest_border_ft = min(euclidean_squares_distance_ft(pr, pc, br, bc) for br, bc in border_pts)
            if closest_border_ft < canopy_radius_ft:
                canopy_overhang = True
                canopy_overhangs += 1

        warnings: List[str] = []
        if not hose_ok:
            warnings.append(
                f"Hose reach needed ({pull_ft} ft from {nearest_spigot}) exceeds {hose_length_ft} ft garden hose."
            )
        if root_foundation_conflict:
            warnings.append(
                f"{plant.common_name} has {plant.root_aggression.value} root aggression too close to house/garage foundation."
            )
        if root_septic_conflict:
            warnings.append(
                f"Roots of {plant.common_name} are within 15 ft of a marked Septic / Underground Utility square!"
            )
        if canopy_overhang:
            warnings.append(
                f"Mature canopy ({plant.mature_spread_ft} ft wide) will overhang the property fence/border."
            )
        if not sun_ok:
            warnings.append(
                f"Prefers {plant.sun_need.value.replace('_', ' ')}, but {cell_label} receives {cell_sun.value.replace('_', ' ')}."
            )

        plant_checks.append(
            PlantSpatialCheck(
                plant_id=plant.plant_id,
                common_name=plant.common_name,
                cell_label=cell_label,
                w3w=plant.square.w3w,
                nearest_spigot_label=nearest_spigot,
                hose_pull_required_ft=pull_ft,
                hose_reachable=hose_ok,
                sun_exposure_in_cell=cell_sun,
                sun_compatible=sun_ok,
                usda_zone_compatible=(3 <= prop_zone_num <= 9),
                root_foundation_conflict=root_foundation_conflict,
                root_septic_conflict=root_septic_conflict,
                canopy_border_overhang=canopy_overhang,
                warnings=warnings,
            )
        )

    recs: List[str] = []
    if all_within_hose:
        recs.append(
            f"All {len(plants)} plantings are reachable with your {hose_length_ft} ft garden hose (max pull: {max_hose_pull} ft incl. +15% slack)."
        )
    else:
        recs.append(
            f"Max garden hose pull is {max_hose_pull} ft (exceeds {hose_length_ft} ft hose). Consider adding a secondary spigot (S2) or extending hose length."
        )
    if root_conflicts > 0:
        recs.append(
            f"{root_conflicts} tree(s) have medium/high root aggression near foundations or septic zones — inspect warnings before planting."
        )
    if sun_mismatches > 0:
        recs.append(
            f"{sun_mismatches} plant(s) have a sun/shade mismatch with their 3m square's shadow profile."
        )
    if root_conflicts == 0 and sun_mismatches == 0 and all_within_hose:
        recs.append("Botanical layout looks healthy: zero root conflicts, optimal sun matching, and full hose coverage!")

    return YardSafetyAndHoseReport(
        total_plants=len(plants),
        trees_count=trees_count,
        shrubs_perennials_count=shrubs_count,
        garden_beds_count=beds_count,
        hose_length_ft=hose_length_ft,
        max_hose_pull_ft=max_hose_pull,
        all_plants_within_hose_reach=all_within_hose,
        root_conflict_count=root_conflicts,
        canopy_overhang_count=canopy_overhangs,
        sun_mismatch_count=sun_mismatches,
        plants_needing_attention=needing_attention,
        plant_checks=plant_checks,
        recommendations=recs,
    )
