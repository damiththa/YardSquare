"""
Google Cloud Platform Direct Persistence Layer (`app/services/gcp_store.py`).

Uses:
- Google Cloud Firestore (`google-cloud-firestore`) for environment-isolated yard storage:
  - `{prefix}yards` (`dev_yards` vs `prod_yards`)
- Google Cloud Storage (`google-cloud-storage`) for plant & tree growth timeline photos.

Zero SQLite or external third-party database is used. When GCP Application Default Credentials (ADC)
are not mounted locally (e.g., during automated CI tests or local offline preview), this service
transparently mirrors the Firestore document collections in memory.
"""

import hashlib
import re
import uuid
from datetime import datetime
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from app.config import get_settings
from app.models import (
    CareActionType,
    CareLogCreateRequest,
    CareLogEntry,
    GridSquareCoord,
    ParcelZoneMap,
    PlantCreateRequest,
    PlantHealthStatus,
    PlantPhoto,
    PlantedItem,
    SubCellSlot,
    YardMap,
)
from app.services.geo_grid import (
    analyze_yard_layout,
    build_grid_square_coord,
    extract_actual_land_and_footprints,
    recalculate_border_polygon_from_zones,
    resolve_usda_hardiness_zone,
)
from app.services.plant_catalog import get_species_by_id


class GCPDataStore:
    """Direct Google Cloud Firestore & Cloud Storage client with DEV/PROD collection isolation."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.firestore_client = None
        self.storage_client = None
        self.gcp_live_connected = False

        self._mem_collections: Dict[str, Dict[str, Dict[str, Any]]] = {
            self.settings.collection_name("yards"): {},
        }

        self._init_gcp_clients()
        if self.settings.SEED_DEMO_DATA:
            self._seed_demo_yards()

    def _init_gcp_clients(self) -> None:
        """Attempt direct connection to Google Cloud Firestore & Cloud Storage."""
        try:
            import google.auth
            from google.cloud import firestore, storage

            credentials, project = google.auth.default()
            if credentials is not None:
                target_project = self.settings.GCP_PROJECT_ID or project
                if target_project:
                    self.firestore_client = firestore.Client(
                        project=target_project,
                        database=self.settings.FIRESTORE_DATABASE,
                        credentials=credentials,
                    )
                    self.storage_client = storage.Client(
                        project=target_project,
                        credentials=credentials,
                    )
                    self.gcp_live_connected = True
        except Exception:
            self.gcp_live_connected = False

    @staticmethod
    def _to_firestore_doc(data: Dict[str, Any]) -> Dict[str, Any]:
        """Convert nested arrays (e.g. border_polygon_lat_lng [[lat, lng], ...]) into Firestore-valid maps."""
        import copy
        doc = copy.deepcopy(data)
        zones = doc.get("zones")
        if isinstance(zones, dict) and isinstance(zones.get("border_polygon_lat_lng"), list):
            converted = []
            for pt in zones["border_polygon_lat_lng"]:
                if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                    converted.append({"lat": float(pt[0]), "lng": float(pt[1])})
                elif isinstance(pt, dict):
                    converted.append(pt)
            zones["border_polygon_lat_lng"] = converted
        return doc

    @staticmethod
    def _from_firestore_doc(data: Dict[str, Any]) -> Dict[str, Any]:
        """Restore Firestore map points [{'lat': ..., 'lng': ...}] back to [[lat, lng], ...]."""
        import copy
        doc = copy.deepcopy(data)
        zones = doc.get("zones")
        if isinstance(zones, dict) and isinstance(zones.get("border_polygon_lat_lng"), list):
            restored = []
            for pt in zones["border_polygon_lat_lng"]:
                if isinstance(pt, dict) and "lat" in pt and "lng" in pt:
                    restored.append([float(pt["lat"]), float(pt["lng"])])
                elif isinstance(pt, (list, tuple)) and len(pt) >= 2:
                    restored.append([float(pt[0]), float(pt[1])])
            zones["border_polygon_lat_lng"] = restored
        return doc

    def _set_doc(self, base_collection: str, doc_id: str, data: Dict[str, Any]) -> None:
        col_name = self.settings.collection_name(base_collection)
        self._mem_collections.setdefault(col_name, {})[doc_id] = data
        if self.gcp_live_connected and self.firestore_client is not None:
            try:
                fs_payload = self._to_firestore_doc(data)
                self.firestore_client.collection(col_name).document(doc_id).set(fs_payload)
            except Exception:
                pass

    def _get_doc(self, base_collection: str, doc_id: str) -> Optional[Dict[str, Any]]:
        col_name = self.settings.collection_name(base_collection)
        if self.gcp_live_connected and self.firestore_client is not None:
            try:
                snap = self.firestore_client.collection(col_name).document(doc_id).get()
                if snap.exists:
                    raw = snap.to_dict()
                    if raw:
                        restored = self._from_firestore_doc(raw)
                        self._mem_collections.setdefault(col_name, {})[doc_id] = restored
                        return restored
            except Exception:
                pass
        return self._mem_collections.get(col_name, {}).get(doc_id)

    def _list_docs(self, base_collection: str) -> List[Dict[str, Any]]:
        col_name = self.settings.collection_name(base_collection)
        if self.gcp_live_connected and self.firestore_client is not None:
            try:
                docs = [
                    self._from_firestore_doc(doc.to_dict())
                    for doc in self.firestore_client.collection(col_name).stream()
                    if doc.exists
                ]
                if docs:
                    return docs
            except Exception:
                pass
        return list(self._mem_collections.get(col_name, {}).values())


    def _build_planted_item_from_catalog(
        self,
        center_lat: float,
        center_lng: float,
        species_id: str,
        row: int,
        col: int,
        sub_slot: SubCellSlot = SubCellSlot.CENTER,
        nickname: Optional[str] = None,
        planted_date: str = "2024-05-12",
        age_years: float = 2.0,
        health_status: PlantHealthStatus = PlantHealthStatus.THRIVING,
        notes: Optional[str] = None,
    ) -> PlantedItem:
        spec = get_species_by_id(species_id)
        if not spec:
            raise ValueError(f"Unknown species_id: {species_id}")
        sq = build_grid_square_coord(center_lat, center_lng, row, col, resolve_w3w=True)
        now_iso = datetime.utcnow().isoformat() + "Z"
        return PlantedItem(
            plant_id=f"plt_{uuid.uuid4().hex[:8]}",
            species_id=spec.species_id,
            common_name=spec.common_name,
            scientific_name=spec.scientific_name,
            nickname=nickname or spec.common_name,
            category=spec.category,
            emoji=spec.emoji,
            square=sq,
            sub_slot=sub_slot,
            planted_date=planted_date,
            age_years=age_years,
            health_status=health_status,
            sun_need=spec.sun_need,
            water_interval_days=spec.water_interval_days,
            last_watered_at=now_iso,
            mature_spread_ft=spec.mature_spread_ft,
            mature_height_ft=spec.mature_height_ft,
            root_aggression=spec.root_aggression,
            seasonal_colors=spec.seasonal_colors,
            notes=notes or spec.care_summary,
            care_logs=[
                CareLogEntry(
                    log_id=f"log_{uuid.uuid4().hex[:6]}",
                    action=CareActionType.WATERED,
                    note="Deep root soak & seasonal check",
                    logged_at=now_iso,
                )
            ],
        )

    def _seed_demo_yards(self) -> None:
        """Seed a friendly default botanical yard so the UI is immediately interactive."""
        if self._get_doc("yards", "yard_default"):
            return

        address = "742 Evergreen Terrace, Montpelier, VT 05602"
        lat = self.settings.DEFAULT_FALLBACK_LAT
        lng = self.settings.DEFAULT_FALLBACK_LNG
        usda_zone, usda_temp = resolve_usda_hardiness_zone(lat, address)
        zones = extract_actual_land_and_footprints(lat, lng, street_address=address)

        plants = [
            self._build_planted_item_from_catalog(
                lat, lng, "malus_honeycrisp", row=3, col=6, sub_slot=SubCellSlot.CENTER,
                nickname="West Orchard Honeycrisp", planted_date="2023-04-20", age_years=3.5,
                health_status=PlantHealthStatus.FRUITING,
                notes="Planted in sunny NW lawn; yielded 24 crisp apples last October.",
            ),
            self._build_planted_item_from_catalog(
                lat, lng, "prunus_bing_cherry", row=3, col=9, sub_slot=SubCellSlot.CENTER,
                nickname="Backyard Sweet Cherry", planted_date="2024-05-01", age_years=2.5,
                health_status=PlantHealthStatus.BLOOMING,
            ),
            self._build_planted_item_from_catalog(
                lat, lng, "acer_palmatum", row=11, col=16, sub_slot=SubCellSlot.CENTER,
                nickname="Front Walkway Japanese Maple", planted_date="2021-09-15", age_years=5.0,
                health_status=PlantHealthStatus.THRIVING,
            ),
            self._build_planted_item_from_catalog(
                lat, lng, "hydrangea_macrophylla", row=5, col=12, sub_slot=SubCellSlot.NW,
                nickname="Patio North Hydrangea (Left)", planted_date="2024-06-10", age_years=2.0,
                health_status=PlantHealthStatus.BLOOMING,
            ),
            self._build_planted_item_from_catalog(
                lat, lng, "hosta_sieboldiana", row=5, col=12, sub_slot=SubCellSlot.NE,
                nickname="Shade Border Hosta", planted_date="2024-06-10", age_years=2.0,
                health_status=PlantHealthStatus.THRIVING,
            ),
            self._build_planted_item_from_catalog(
                lat, lng, "solanum_lycopersicum", row=3, col=15, sub_slot=SubCellSlot.CENTER,
                nickname="Sunny Raised Bed #1 (Tomatoes)", planted_date="2026-05-25", age_years=0.5,
                health_status=PlantHealthStatus.NEEDS_WATER,
            ),
            self._build_planted_item_from_catalog(
                lat, lng, "ocimum_basilicum", row=3, col=15, sub_slot=SubCellSlot.SE,
                nickname="Companion Sweet Basil", planted_date="2026-05-25", age_years=0.5,
                health_status=PlantHealthStatus.THRIVING,
            ),
            self._build_planted_item_from_catalog(
                lat, lng, "thuja_green_giant", row=2, col=20, sub_slot=SubCellSlot.CENTER,
                nickname="NE Corner Privacy Arborvitae", planted_date="2022-04-10", age_years=4.0,
                health_status=PlantHealthStatus.THRIVING,
            ),
        ]

        analysis = analyze_yard_layout(
            zones=zones,
            plants=plants,
            usda_zone=usda_zone,
        )

        default_yard = YardMap(
            yard_id="yard_default",
            yard_name="Evergreen Terrace Sanctuary",
            owner_name="Homeowner",
            street_address=address,
            parcel_center_lat=lat,
            parcel_center_lng=lng,
            usda_zone=usda_zone,
            usda_min_temp_f=usda_temp,
            grid_rows=self.settings.DEFAULT_GRID_ROWS,
            grid_cols=self.settings.DEFAULT_GRID_COLS,
            square_size_meters=self.settings.SQUARE_SIZE_METERS,
            square_size_feet=round(self.settings.square_size_feet, 2),
            zones=zones,
            plants=plants,
            analysis=analysis,
        )
        self._set_doc("yards", default_yard.yard_id, default_yard.model_dump())

    # ------------------------------------------------------------------
    # Yard CRUD, Address-Keyed Persistence (Option A) & Spatial Recalculation
    # ------------------------------------------------------------------
    @staticmethod
    def normalize_address_key(street_address: str) -> str:
        """Normalize a street address so typing the same address later resolves to the exact same key."""
        cleaned = re.sub(r",\s*(usa|united states)$", "", (street_address or "").strip(), flags=re.IGNORECASE)
        cleaned = re.sub(r"[^a-z0-9]+", " ", cleaned.lower()).strip()
        return cleaned

    @classmethod
    def address_to_yard_id(cls, street_address: str) -> str:
        """
        Option A Deterministic Property Key:
        Maps any searched street address to its own dedicated Firestore document (`yard_addr_<hash>`)
        so users never overwrite `yard_default` and can always resume by typing their address again.
        """
        norm = cls.normalize_address_key(street_address)
        if not norm or "742 evergreen terrace" in norm:
            return "yard_default"
        digest = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:10]
        return f"yard_addr_{digest}"

    def get_yard(self, yard_id: str = "yard_default") -> Optional[YardMap]:
        data = self._get_doc("yards", yard_id)
        return YardMap(**data) if data else None

    def list_yards(self) -> List[YardMap]:
        return [YardMap(**d) for d in self._list_docs("yards")]

    def find_yard_by_address(self, street_address: str) -> Optional[YardMap]:
        """Find a previously saved YardMap matching the given street address so custom borders are remembered."""
        norm_target = self.normalize_address_key(street_address)
        if not norm_target:
            return None
        # 1. Direct O(1) lookup by deterministic address yard_id
        det_id = self.address_to_yard_id(street_address)
        direct_yard = self.get_yard(det_id)
        if direct_yard and self.normalize_address_key(direct_yard.street_address) == norm_target:
            return direct_yard
        # 2. Fallback scan across saved yards
        for yard in self.list_yards():
            saved_norm = self.normalize_address_key(yard.street_address)
            if saved_norm == norm_target or (
                len(norm_target) >= 8 and (norm_target in saved_norm or saved_norm in norm_target)
            ):
                return yard
        return None

    def resolve_or_fork_yard_for_geocoded_parcel(
        self,
        parcel: Dict[str, Any],
        requested_yard_id: Optional[str] = None,
        force_reset_border: bool = False,
    ) -> Tuple[YardMap, bool]:
        """
        Option A (Address-Keyed Cloud Persistence + Auto-Forking):
        - Never overwrites `yard_default` when a user searches a real street address.
        - Automatically creates or loads the dedicated Firestore document for that street address.
        - Restores any previously saved custom polygon boundaries, structures, and planted items.
        """
        new_fmt = (parcel.get("formatted_address") or "").strip()
        det_yard_id = self.address_to_yard_id(new_fmt)

        existing_yard: Optional[YardMap] = None
        if requested_yard_id:
            req_yard = self.get_yard(requested_yard_id)
            if req_yard and self.normalize_address_key(req_yard.street_address) == self.normalize_address_key(new_fmt):
                existing_yard = req_yard

        if existing_yard is None:
            existing_yard = self.find_yard_by_address(new_fmt)

        if existing_yard is not None:
            existing_yard.street_address = new_fmt
            existing_yard.parcel_center_lat = parcel["parcel_center_lat"]
            existing_yard.parcel_center_lng = parcel["parcel_center_lng"]
            existing_yard.usda_zone = parcel["usda_zone"]
            existing_yard.usda_min_temp_f = parcel["usda_min_temp_f"]

            if (
                not force_reset_border
                and existing_yard.zones
                and existing_yard.zones.footprint_source == "user_adjusted_custom_border"
            ):
                saved = self.save_yard(existing_yard)
                return saved, True

            existing_yard.zones = ParcelZoneMap(**parcel["zones"])
            saved = self.save_yard(existing_yard)
            return saved, False

        short_street = new_fmt.split(",")[0].strip() if new_fmt else "My Home"
        forked_yard = YardMap(
            yard_id=det_yard_id,
            yard_name=f"{short_street} Yard",
            owner_name="Homeowner",
            street_address=new_fmt,
            parcel_center_lat=parcel["parcel_center_lat"],
            parcel_center_lng=parcel["parcel_center_lng"],
            usda_zone=parcel["usda_zone"],
            usda_min_temp_f=parcel["usda_min_temp_f"],
            grid_rows=self.settings.DEFAULT_GRID_ROWS,
            grid_cols=self.settings.DEFAULT_GRID_COLS,
            square_size_meters=self.settings.SQUARE_SIZE_METERS,
            square_size_feet=round(self.settings.square_size_feet, 2),
            zones=ParcelZoneMap(**parcel["zones"]),
            plants=[],
        )
        saved = self.save_yard(forked_yard)
        return saved, False

    def update_yard_borders(
        self,
        yard_id: str,
        zones: ParcelZoneMap,
        yard_name: Optional[str] = None,
    ) -> YardMap:
        """Persist user-adjusted fence/property border cells and recompute GPS border polygon & safety checks."""
        yard = self.get_yard(yard_id)
        if not yard:
            raise ValueError(f"Yard not found: {yard_id}")
        zones.footprint_source = "user_adjusted_custom_border"
        yard.zones = zones
        if yard_name:
            yard.yard_name = yard_name.strip()
        return self.save_yard(yard)

    def save_yard(self, yard: YardMap) -> YardMap:
        for p in yard.plants:
            p.square = build_grid_square_coord(
                yard.parcel_center_lat, yard.parcel_center_lng, p.square.row, p.square.col, resolve_w3w=True
            )

        if yard.zones is not None:
            yard.zones.border_polygon_lat_lng = recalculate_border_polygon_from_zones(
                yard.parcel_center_lat,
                yard.parcel_center_lng,
                yard.zones,
                grid_rows=yard.grid_rows,
                grid_cols=yard.grid_cols,
            )

        yard.analysis = analyze_yard_layout(
            zones=yard.zones,
            plants=yard.plants,
            usda_zone=yard.usda_zone,
        )
        yard.updated_at = datetime.utcnow().isoformat() + "Z"
        self._set_doc("yards", yard.yard_id, yard.model_dump())
        return yard

    def add_plant_to_yard(self, yard_id: str, req: PlantCreateRequest) -> YardMap:
        yard = self.get_yard(yard_id)
        if not yard:
            raise ValueError(f"Yard not found: {yard_id}")

        new_plant = self._build_planted_item_from_catalog(
            center_lat=yard.parcel_center_lat,
            center_lng=yard.parcel_center_lng,
            species_id=req.species_id,
            row=req.row,
            col=req.col,
            sub_slot=req.sub_slot,
            nickname=req.nickname,
            planted_date=req.planted_date or datetime.utcnow().strftime("%Y-%m-%d"),
            age_years=req.age_years,
            health_status=req.health_status,
            notes=req.notes,
        )
        yard.plants.append(new_plant)
        return self.save_yard(yard)

    def remove_plant_from_yard(self, yard_id: str, plant_id: str) -> YardMap:
        yard = self.get_yard(yard_id)
        if not yard:
            raise ValueError(f"Yard not found: {yard_id}")
        yard.plants = [p for p in yard.plants if p.plant_id != plant_id]
        return self.save_yard(yard)

    def log_plant_care(self, yard_id: str, plant_id: str, req: CareLogCreateRequest) -> YardMap:
        yard = self.get_yard(yard_id)
        if not yard:
            raise ValueError(f"Yard not found: {yard_id}")
        now_iso = datetime.utcnow().isoformat() + "Z"
        for p in yard.plants:
            if p.plant_id == plant_id:
                p.care_logs.insert(
                    0,
                    CareLogEntry(
                        log_id=f"log_{uuid.uuid4().hex[:6]}",
                        action=req.action,
                        note=req.note or f"Logged {req.action.value} care check",
                        logged_at=now_iso,
                    ),
                )
                if req.action == CareActionType.WATERED:
                    p.last_watered_at = now_iso
                    if p.health_status == PlantHealthStatus.NEEDS_WATER:
                        p.health_status = PlantHealthStatus.THRIVING
                if req.health_status is not None:
                    p.health_status = req.health_status
                break
        return self.save_yard(yard)

    def upload_plant_photo(
        self,
        yard_id: str,
        plant_id: str,
        filename: str,
        content_bytes: bytes,
        content_type: str = "image/jpeg",
        caption: Optional[str] = None,
        season_tag: str = "summer",
    ) -> PlantPhoto:
        photo_id = f"photo_{uuid.uuid4().hex[:8]}"
        ext = filename.split(".")[-1] if "." in filename else "jpg"
        blob_path = f"{self.settings.ENV}/{yard_id}/{plant_id}/{photo_id}.{ext}"
        gcs_uri = f"gs://{self.settings.effective_gcs_bucket}/{blob_path}"
        public_url = f"https://storage.googleapis.com/{self.settings.effective_gcs_bucket}/{blob_path}"

        if self.gcp_live_connected and self.storage_client is not None:
            try:
                bucket = self.storage_client.bucket(self.settings.effective_gcs_bucket)
                blob = bucket.blob(blob_path)
                blob.upload_from_string(content_bytes, content_type=content_type)
            except Exception:
                pass

        photo = PlantPhoto(
            photo_id=photo_id,
            gcs_uri=gcs_uri,
            public_url=public_url,
            caption=caption or "Seasonal growth photo",
            season_tag=season_tag,
        )
        yard = self.get_yard(yard_id)
        if yard:
            for p in yard.plants:
                if p.plant_id == plant_id:
                    p.photos.insert(0, photo)
                    break
            self.save_yard(yard)
        return photo


@lru_cache()
def get_store() -> GCPDataStore:
    return GCPDataStore()
