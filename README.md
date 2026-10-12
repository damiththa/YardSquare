# 🌳 YardSquare — Living 3m × 3m What3Words Yard & Botanical Companion

[![YardSquare Archify System Architecture](./docs/architecture/yardsquare-architecture.svg)](./docs/ARCHITECTURE.md)

**YardSquare** (`https://github.com/damiththa/YardSquare`) is a 100% Google Cloud Platform (GCP) native residential plant, tree, and garden bed tracker built on a **3m × 3m (~9.84 ft × 9.84 ft) What3Words property grid**.

---

## ✨ Key Capabilities

1. **Split-Provider $0 Address Autocomplete & Actual Land Extraction:**
   - **Stage 1 ($0.00):** Instant local catalog + **Photon (OpenStreetMap)** type-ahead suggestions (`0` Google Maps quota used).
   - **Stage 2 (Rooftop Geocode + OSM Overpass):** Resolves rooftop coordinates, queries **OpenStreetMap Overpass** for actual building footprints (`way["building"]`), and detects the property's **USDA Plant Hardiness Zone**.
2. **Interactive Property Border & Land Adjustment (`✏️ Adjust Land & Border`):**
   - Click or drag across 3m × 3m squares to customize your exact **Property Border / Fence Line**, **House Roof**, **Garage / Shed**, **Patio / Driveway**, **Septic / Utility Zones**, and **Out-of-Bounds** areas.
3. **3m × 3m What3Words Grid + 3×3 (1m × 1m) Sub-Square Inspector:**
   - Every 3m × 3m cell has a human-readable code (`R4-C7`), exact WGS84 `(lat, lng)`, sun/shade exposure (`Full Sun`, `Part Shade`, `Full Shade`), and on-demand `///what.three.words` address.
   - Each 3m square subdivides into a **3×3 micro-grid (`NW, N, NE, W, C, E, SW, S, SE`)** so you can plant a single fruit/shade tree in the center or up to 9 shrubs/vegetables in a raised bed.
4. **Cozy "Come-Back-To" Companion Experience:**
   - **Seasonal Switcher:** Preview your yard across **🌸 Spring**, **☀️ Summer**, **🍂 Autumn**, and **❄️ Winter**.
   - **Canopy Time-Travel:** Toggle between **🌱 Today**, **🌿 +5 Years**, and **🌳 +15 Years (Mature Canopy)**.
   - **Spatial Safety & Garden Hose Reach:** Automatically checks if your 50–150 ft garden hose from Spigots `S1`/`S2` reaches all planted squares (+15% slack) and flags trees with aggressive roots near house foundations or septic lines.

---

## 🏗️ Cloud-Direct GCP Stack & Tier-1 ($0.00) Cost Guardrails

- **Compute:** Google Cloud Run (`yardsquare-api-dev` & `yardsquare-api-prod`)
- **Database:** Google Cloud Firestore (`dev_yards` vs `prod_yards`) + transparent in-memory local fallback
- **Media Storage:** Google Cloud Storage (`gs://{project}-media/{env}/...`)
- **Secrets:** Google Cloud Secret Manager (`app/services/secret_manager.py`) — **Zero hardcoded secrets or values**
- **Architecture:** [Archify v3.0 System Architecture](./docs/ARCHITECTURE.md) (`/architecture`)

---

## 🚀 Local Development & Testing

```bash
pip install -r requirements.txt
pytest -v
python3 scripts/render_archify.py
uvicorn app.main:app --reload --port 8080
```
