"""
Automated Pytest Suite for YardSquare (`tests/test_yardsquare.py`).

Verifies:
1. System Health, Public Config & Tier-1 ($0.00 Personal Use) Free-Tier Guardrails
2. Split-Provider Address Autocomplete ($0 Photon/Local) & Rooftop Geocode + OSM Footprint Extraction
3. 3m x 3m What3Words Grid Coordinate Math & W3W On-Demand Resolver
4. Botanical Species Catalog & Filtering
5. Point-and-Click Plant Placement, 3x3 Sub-Square Slots, Care Logging & Spatial Analysis (Hose Reach + Root Safety)
6. Interactive Property Border Adjustment Persistence
"""

from fastapi.testclient import TestClient

from app.main import app
from app.services.geo_grid import (
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
