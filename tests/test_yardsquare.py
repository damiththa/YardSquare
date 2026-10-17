"""
Automated Pytest Suite for YardSquare (`tests/test_yardsquare.py`).

Verifies:
1. System Health, Public Config & Tier-1 ($0.00 Personal Use) Free-Tier Guardrails
2. Split-Provider Address Autocomplete ($0 Photon/Local) & Rooftop Geocode + OSM Footprint Extraction
3. 3m x 3m What3Words Grid Coordinate Math & W3W On-Demand Resolver
4. Botanical Species Catalog & Filtering
5. Point-and-Click Plant Placement, 3x3 Sub-Square Slots, Care Logging & Spatial Analysis (Root & Canopy Safety)
6. Interactive Property Border Adjustment & Option A Address-Keyed Cloud Persistence
"""

import struct
import zlib

from fastapi.testclient import TestClient

from app.main import app
from app.services.geo_grid import (
    _decode_png_to_cell_patches,
    compute_grid_bbox_wgs84,
    euclidean_squares_distance_ft,
    format_cell_label,
    grid_cell_to_lat_lng,
    lat_lng_to_grid_cell,
)

client = TestClient(app)


def test_health_and_tier1_free_cap() -> None:
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["app_name"] == "YardSquare"
    assert data["maps_free_tier_guardrail"]["pay_as_you_go_blocked"] is True


def test_3m_grid_roundtrip_math() -> None:
    center_lat, center_lng = 44.2601, -72.5754
    lat, lng = grid_cell_to_lat_lng(center_lat, center_lng, row=3, col=6)
    r_back, c_back = lat_lng_to_grid_cell(center_lat, center_lng, lat, lng)
    assert (r_back, c_back) == (3, 6)
    assert format_cell_label(3, 6) == "R4-C7"
    dist = euclidean_squares_distance_ft(0, 0, 3, 4)
    assert round(dist, 1) == 49.2  # 5 squares * 9.84252 ft


def test_split_provider_autocomplete_and_geocode() -> None:
    auto_resp = client.get("/api/v1/yard/autocomplete?q=742+Evergreen")
    assert auto_resp.status_code == 200
    auto_data = auto_resp.json()
    assert auto_data["google_quota_used"] == 0
    assert auto_data["cost_usd"] == 0.0
    assert len(auto_data["suggestions"]) >= 1

    geo_resp = client.post(
        "/api/v1/yard/geocode",
        json={"street_address": "742 Evergreen Terrace, Montpelier, VT 05602"},
    )
    assert geo_resp.status_code == 200
    geo_data = geo_resp.json()
    assert "usda_zone" in geo_data
    assert "zones" in geo_data
    assert len(geo_data["zones"]["house_roof_cells"]) > 0
    assert len(geo_data["zones"]["fence_border_cells"]) > 0


def test_w3w_on_demand_resolver() -> None:
    resp = client.get("/api/v1/w3w/convert-to-3wa?row=3&col=6")
    assert resp.status_code == 200
    data = resp.json()
    assert data["label"] == "R4-C7"
    assert data["w3w"].startswith("///")
    assert len(data["w3w"].split(".")) == 3


def test_catalog_and_plant_lifecycle() -> None:
    cat_resp = client.get("/api/v1/catalog/species?q=apple")
    assert cat_resp.status_code == 200
    species = cat_resp.json()
    assert any(s["species_id"] == "malus_honeycrisp" for s in species)

    yard_resp = client.get("/api/v1/yard/yard_default")
    assert yard_resp.status_code == 200
    initial_count = len(yard_resp.json()["plants"])

    # Add a new Highbush Blueberry into R5-C8 [NE]
    add_resp = client.post(
        "/api/v1/yard/yard_default/plants",
        json={
            "species_id": "vaccinium_corymbosum",
            "nickname": "Backyard Blueberry #1",
            "row": 4,
            "col": 7,
            "sub_slot": "NE",
        },
    )
    assert add_resp.status_code == 200
    updated_yard = add_resp.json()
    assert len(updated_yard["plants"]) == initial_count + 1
    added_plant = updated_yard["plants"][-1]
    assert added_plant["square"]["label"] == "R5-C8"
    assert added_plant["sub_slot"] == "NE"

    # Log a 1-tap care event
    care_resp = client.post(
        f"/api/v1/yard/yard_default/plants/{added_plant['plant_id']}/care",
        json={"action": "watered", "note": "Morning watering test"},
    )
    assert care_resp.status_code == 200

    # Delete the added plant to keep state clean
    del_resp = client.delete(f"/api/v1/yard/yard_default/plants/{added_plant['plant_id']}")
    assert del_resp.status_code == 200
    assert len(del_resp.json()["plants"]) == initial_count


def test_custom_fence_border_adjustment_and_memory() -> None:
    yard_resp = client.get("/api/v1/yard/yard_default")
    assert yard_resp.status_code == 200
    yard = yard_resp.json()
    zones = yard["zones"]

    # Customize the fence perimeter to a specific set of 3m squares
    custom_fence = ["R3-C6", "R3-C7", "R3-C8", "R14-C6", "R14-C20"]
    zones["fence_border_cells"] = custom_fence

    border_resp = client.put(
        "/api/v1/yard/yard_default/borders",
        json={"zones": zones, "yard_name": "Evergreen Custom Fenced Yard"},
    )
    assert border_resp.status_code == 200
    saved_yard = border_resp.json()
    assert saved_yard["zones"]["footprint_source"] == "user_adjusted_custom_border"
    assert saved_yard["zones"]["fence_border_cells"] == custom_fence
    assert len(saved_yard["zones"]["border_polygon_lat_lng"]) == 4

    # Verify re-geocoding the same address remembers the custom fence unless force_reset_border=True
    geo_keep = client.post(
        "/api/v1/yard/geocode",
        json={
            "street_address": yard["street_address"],
            "apply_to_yard_id": "yard_default",
            "force_reset_border": False,
        },
    )
    assert geo_keep.status_code == 200
    assert geo_keep.json()["remembered_custom_border"] is True
    assert geo_keep.json()["zones"]["fence_border_cells"] == custom_fence

    # Verify force_reset_border=True restores default footprint
    geo_reset = client.post(
        "/api/v1/yard/geocode",
        json={
            "street_address": yard["street_address"],
            "apply_to_yard_id": "yard_default",
            "force_reset_border": True,
        },
    )
    assert geo_reset.status_code == 200
    assert geo_reset.json()["remembered_custom_border"] is False
    assert geo_reset.json()["zones"]["footprint_source"] != "user_adjusted_custom_border"


def test_custom_polygon_vertices_l_shaped_boundary() -> None:
    yard_resp = client.get("/api/v1/yard/yard_default")
    assert yard_resp.status_code == 200
    zones = yard_resp.json()["zones"]

    # Define a 6-corner L-shaped polygon boundary on the 3m grid
    l_shape_vertices = [
        {"row": 1, "col": 3},
        {"row": 1, "col": 22},
        {"row": 8, "col": 22},
        {"row": 8, "col": 17},
        {"row": 16, "col": 17},
        {"row": 16, "col": 3},
    ]
    zones["border_vertices"] = l_shape_vertices
    zones["fence_border_cells"] = []  # Let backend rasterize from polygon vertices

    border_resp = client.put(
        "/api/v1/yard/yard_default/borders",
        json={"zones": zones, "yard_name": "Evergreen L-Shaped Polygon Yard"},
    )
    assert border_resp.status_code == 200
    saved = border_resp.json()
    assert saved["zones"]["footprint_source"] == "user_adjusted_custom_border"
    assert len(saved["zones"]["border_vertices"]) == 6
    assert len(saved["zones"]["border_polygon_lat_lng"]) == 6
    assert len(saved["zones"]["fence_border_cells"]) > 20
    assert "R12-C21" in saved["zones"]["out_of_bounds_cells"]


def test_option_a_address_keyed_auto_fork_and_persistence() -> None:
    # Searching a brand-new address while viewing yard_default must NOT overwrite yard_default;
    # instead it auto-creates a deterministic yard_addr_<hash> document.
    geo1 = client.post(
        "/api/v1/yard/geocode",
        json={
            "street_address": "1600 Pennsylvania Ave NW, Washington, DC 20500",
            "apply_to_yard_id": "yard_default",
            "force_reset_border": False,
        },
    )
    assert geo1.status_code == 200
    data1 = geo1.json()
    forked_id = data1["yard_id"]
    assert forked_id.startswith("yard_addr_")
    assert forked_id != "yard_default"

    # Verify yard_default was NOT overwritten
    default_yard = client.get("/api/v1/yard/yard_default").json()
    assert "Evergreen Terrace" in default_yard["street_address"]

    # Save a custom fence + custom garage box on the forked address yard
    zones = data1["zones"]
    zones["garage_shed_cells"] = ["R6-C15", "R6-C16", "R7-C15", "R7-C16"]
    save_resp = client.put(
        f"/api/v1/yard/{forked_id}/borders",
        json={"zones": zones, "yard_name": "Custom DC Property"},
    )
    assert save_resp.status_code == 200

    # Simulate a user returning later on a fresh browser (starting at yard_default)
    # and typing the same street address -> must resolve to forked_id and remember custom structures/fence!
    geo2 = client.post(
        "/api/v1/yard/geocode",
        json={
            "street_address": "1600 Pennsylvania Ave NW, Washington, DC 20500",
            "apply_to_yard_id": "yard_default",
            "force_reset_border": False,
        },
    )
    assert geo2.status_code == 200
    data2 = geo2.json()
    assert data2["yard_id"] == forked_id
    assert data2["remembered_custom_border"] is True
    assert "R6-C15" in data2["zones"]["garage_shed_cells"]


def test_satellite_png_patch_decoder_and_bbox_math() -> None:
    west_lng, south_lat, east_lng, north_lat = compute_grid_bbox_wgs84(44.2601, -72.5754, 18, 26)
    assert west_lng < -72.5754 < east_lng
    assert south_lat < 44.2601 < north_lat

    # Build a synthetic 26x18 RGB PNG24 image in memory (green lawn + gray roof/driveway center)
    w, h = 26, 18
    raw_scanlines = bytearray()
    for r in range(h):
        raw_scanlines.append(0)  # filter_type = 0 (None)
        for c in range(w):
            if 7 <= r <= 10 and 11 <= c <= 14:
                # Gray roof / paved driveway patch
                raw_scanlines.extend((145, 145, 145))
            else:
                # Vibrant green lawn patch
                raw_scanlines.extend((55, 150, 45))

    def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    idat = zlib.compress(bytes(raw_scanlines))
    png_bytes = b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")

    patches = _decode_png_to_cell_patches(png_bytes, rows=18, cols=26)
    assert patches is not None
    assert len(patches) == 18
    assert len(patches[0]) == 26
    # Center roof cell R9-C13 (index 8, 12) has zero Excess Green Index and zero chroma
    assert patches[8][12]["exg"] == 0.0
    assert patches[8][12]["chroma"] == 0.0
    # Lawn cell R2-C2 (index 1, 1) has high Excess Green Index (2*150 - 55 - 45 = 200)
    assert patches[1][1]["exg"] > 150.0
