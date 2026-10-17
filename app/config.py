"""
YardSquare Configuration Module (`app/config.py`).

Zero Hardcoded Values & Strict Tier-1 ($0.00 Personal Use) Cost Protection Architecture:
- All sensitive keys (`GOOGLE_MAPS_API_KEY`, `W3W_API_KEY`, `PERENUAL_API_KEY`, `PLANTNET_API_KEY`,
  `SESSION_SIGNING_SECRET`) are loaded at runtime via `app/services/secret_manager.py`
  from Cloud Run `--set-secrets` environment bindings or directly from Google Cloud Secret Manager.
- All external endpoint URLs, grid dimensions, and quota caps are
  configurable via environment variables.
- Enforces hard monthly request caps (`MAPS_MONTHLY_FREE_TIER_CAP=9500`, below Google Maps'
  10,000/month free SKU limit) so YardSquare never spills into paid Pay-As-You-Go billing.
"""

import os
from functools import lru_cache
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.services.secret_manager import get_secret


class Settings(BaseSettings):
    """Application, Geospatial & Google Cloud Platform settings (Zero hardcoded secrets)."""

    ENV: str = "dev"  # "dev" or "prod"
    APP_NAME: str = "YardSquare"
    API_VERSION: str = "1.0.0"
    PORT: int = 8080

    # Google Cloud Platform Project & Resource Identifiers (Injected by Cloud Run / CI)
    GCP_PROJECT_ID: str = os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT", "")
    FIRESTORE_DATABASE: str = "(default)"
    FIRESTORE_COLLECTION_PREFIX: str = ""
    GCS_BUCKET_NAME: str = ""
    USE_GCP_SECRET_MANAGER: bool = False
    SECRET_NAME_PREFIX: str = "yardsquare"

    # External API Endpoints (Configurable via env vars, never hardcoded in service functions)
    PHOTON_AUTOCOMPLETE_URL: str = "https://photon.komoot.io/api/"
    GOOGLE_GEOCODE_URL: str = "https://maps.googleapis.com/maps/api/geocode/json"
    OVERPASS_API_URL: str = "https://overpass-api.de/api/interpreter"
    FEMA_STRUCTURES_QUERY_URL: str = (
        "https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services/USA_Structures_View/FeatureServer/0/query"
    )
    ESRI_WORLD_IMAGERY_EXPORT_URL: str = (
        "https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/export"
    )
    USGS_NAIP_EXPORT_URL: str = (
        "https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPPlus/ImageServer/exportImage"
    )
    MAPBOX_STATIC_BASE_URL: str = "https://api.mapbox.com/styles/v1/mapbox/satellite-v9/static"
    W3W_CONVERT_URL: str = "https://api.what3words.com/v3/convert-to-3wa"
    W3W_GRID_SECTION_URL: str = "https://api.what3words.com/v3/grid-section"
    PHZM_API_BASE_URL: str = "https://phzmapi.org"
    USER_AGENT_HEADER: str = "YardSquare-GCP-BotanicalGrid/1.0 (+https://github.com/damiththa/YardSquare)"

    # Grid & Spatial Geometry Parameters
    DEFAULT_GRID_ROWS: int = 18
    DEFAULT_GRID_COLS: int = 26
    SQUARE_SIZE_METERS: float = 3.0
    FEET_PER_METER: float = 3.28084
    DEFAULT_FALLBACK_LAT: float = 44.2601
    DEFAULT_FALLBACK_LNG: float = -72.5754

    # Strict Tier-1 ($0.00 Personal Use) Free-Tier Hard Caps
    ENFORCE_FREE_TIER_HARD_CAP: bool = True
    MAPS_MONTHLY_FREE_TIER_CAP: int = 9500
    MAPS_DAILY_SAFE_CAP: int = 300
    W3W_CACHE_TTL_SECONDS: int = 86400

    # Environment & Security Controls
    ALLOWED_ORIGINS: str = "*"
    SEED_DEMO_DATA: bool = True

    model_config = SettingsConfigDict(
        env_file=".env" if os.path.exists(".env") else None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def is_prod(self) -> bool:
        return self.ENV.lower().strip() == "prod"

    @property
    def square_size_feet(self) -> float:
        return round(self.SQUARE_SIZE_METERS * self.FEET_PER_METER, 4)

    @property
    def effective_collection_prefix(self) -> str:
        if self.FIRESTORE_COLLECTION_PREFIX:
            return self.FIRESTORE_COLLECTION_PREFIX
        return "prod_" if self.is_prod else "dev_"

    @property
    def effective_gcs_bucket(self) -> str:
        if self.GCS_BUCKET_NAME:
            return self.GCS_BUCKET_NAME
        if self.GCP_PROJECT_ID:
            return f"{self.GCP_PROJECT_ID}-media"
        return f"{self.SECRET_NAME_PREFIX}-{self.ENV.lower()}-media"

    @property
    def google_maps_api_key(self) -> str:
        return get_secret(
            "GOOGLE_MAPS_API_KEY",
            gcp_secret_id=f"{self.SECRET_NAME_PREFIX}-google-maps-api-key",
            project_id=self.GCP_PROJECT_ID,
        )

    @property
    def mapbox_access_token(self) -> str:
        return get_secret(
            "MAPBOX_ACCESS_TOKEN",
            gcp_secret_id=f"{self.SECRET_NAME_PREFIX}-mapbox-access-token",
            project_id=self.GCP_PROJECT_ID,
        )

    @property
    def w3w_api_key(self) -> str:
        return get_secret(
            "W3W_API_KEY",
            gcp_secret_id=f"{self.SECRET_NAME_PREFIX}-w3w-api-key",
            project_id=self.GCP_PROJECT_ID,
        )

    @property
    def perenual_api_key(self) -> str:
        return get_secret(
            "PERENUAL_API_KEY",
            gcp_secret_id=f"{self.SECRET_NAME_PREFIX}-perenual-api-key",
            project_id=self.GCP_PROJECT_ID,
        )

    @property
    def plantnet_api_key(self) -> str:
        return get_secret(
            "PLANTNET_API_KEY",
            gcp_secret_id=f"{self.SECRET_NAME_PREFIX}-plantnet-api-key",
            project_id=self.GCP_PROJECT_ID,
        )

    @property
    def session_signing_secret(self) -> str:
        return get_secret(
            "SESSION_SIGNING_SECRET",
            gcp_secret_id=f"{self.SECRET_NAME_PREFIX}-session-signing-secret",
            project_id=self.GCP_PROJECT_ID,
            generate_ephemeral_if_missing=True,
        )

    @property
    def cors_origins(self) -> List[str]:
        if self.ALLOWED_ORIGINS.strip() == "*":
            return ["*"]
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",") if origin.strip()]

    def collection_name(self, base_name: str) -> str:
        """Return environment-isolated Firestore collection name (`dev_yards` vs `prod_yards`)."""
        return f"{self.effective_collection_prefix}{base_name}"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
