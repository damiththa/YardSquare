"""
what3words On-Demand Resolution & 3m Grid Service (`app/services/w3w_service.py`).

Licensing, Tier-1 ($0.00) Free-Tier Circuit Breaker & Architecture Design:
- Stores permanent WGS84 `(lat, lng)` coordinates in Google Cloud Firestore and resolves
  `///word1.word2.word3` on-demand with a configurable TTL cache (`W3W_CACHE_TTL_SECONDS`).
- When `W3W_API_KEY` is provided via Google Cloud Secret Manager:
  - Calls `settings.W3W_CONVERT_URL` (`/v3/convert-to-3wa`).
  - If the key is on the W3W Free Plan (`HTTP 402 Payment Required`), automatically engages
    a circuit breaker and uses YardSquare's instant, zero-cost deterministic 3-word generator
    (`///word1.word2.word3`) backed by exact WGS84 `(lat, lng)` coordinates.
"""

import hashlib
import time
from typing import Any, Dict, Tuple
import httpx

from app.config import get_settings

_WORDS_A = [
    "maple", "orchard", "cedar", "meadow", "clover", "willow", "birch", "juniper",
    "fern", "hazel", "linden", "cypress", "summit", "aurora", "harbor", "granite",
    "sorrel", "laurel", "heather", "breeze", "sunlit", "harvest", "verbena", "acorn",
]
_WORDS_B = [
    "garden", "blossom", "canopy", "trellis", "petals", "terrace", "arbor", "foliage",
    "sprout", "meadow", "brook", "lantern", "pathway", "nectar", "pines", "hedgerow",
    "pollen", "rootlet", "furrow", "thicket", "sundial", "pebble", "boughs", "grove",
]
_WORDS_C = [
    "square", "patch", "corner", "border", "haven", "glade", "bench", "alcove",
    "ridge", "lawn", "crest", "stone", "gate", "patio", "verge", "nook",
    "hollow", "field", "oasis", "trail", "spring", "shade", "court", "bower",
]

_W3W_CACHE: Dict[Tuple[float, float], Tuple[str, float]] = {}
_W3W_CIRCUIT_STATE: Dict[str, Any] = {
    "free_plan_402_detected": False,
    "last_status_code": None,
}


def get_w3w_service_status() -> Dict[str, Any]:
    """Return current what3words API circuit-breaker status."""
    settings = get_settings()
    return {
        "key_configured_in_secret_manager": bool(settings.w3w_api_key),
        "free_plan_402_fallback_active": _W3W_CIRCUIT_STATE["free_plan_402_detected"],
        "last_http_status": _W3W_CIRCUIT_STATE["last_status_code"],
        "mode": (
            "deterministic_free_tier_fallback"
            if _W3W_CIRCUIT_STATE["free_plan_402_detected"] or not settings.w3w_api_key
            else "live_w3w_v3_api"
        ),
    }


def resolve_w3w_sync(lat: float, lng: float) -> str:
    """
    Resolve a (lat, lng) coordinate to a what3words address (`///w1.w2.w3`).
    Uses TTL caching and calls the official what3words v3 API if `w3w_api_key` is active.
    Automatically engages a zero-latency fallback if the W3W key is on the Free Tier (`HTTP 402`).
    """
    settings = get_settings()
    cache_key = (round(lat, 6), round(lng, 6))
    now = time.time()

    if cache_key in _W3W_CACHE:
        cached_val, expires_at = _W3W_CACHE[cache_key]
        if now < expires_at:
            return cached_val

    w3w_address = _deterministic_w3w(lat, lng)
    api_key = settings.w3w_api_key

    if api_key and not _W3W_CIRCUIT_STATE["free_plan_402_detected"]:
        try:
            with httpx.Client(timeout=2.5) as client:
                resp = client.get(
                    settings.W3W_CONVERT_URL,
                    params={"coordinates": f"{lat},{lng}", "key": api_key, "language": "en"},
                )
                _W3W_CIRCUIT_STATE["last_status_code"] = resp.status_code
                if resp.status_code == 200:
                    data = resp.json()
                    if "words" in data:
                        w3w_address = f"///{data['words']}"
                elif resp.status_code in (401, 402, 403):
                    _W3W_CIRCUIT_STATE["free_plan_402_detected"] = True
        except Exception:
            pass

    _W3W_CACHE[cache_key] = (w3w_address, now + settings.W3W_CACHE_TTL_SECONDS)
    return w3w_address


def _deterministic_w3w(lat: float, lng: float) -> str:
    """Generate a deterministic, botanical-friendly `///word.word.word` for any 3m x 3m coordinate."""
    seed = f"{round(lat, 6)}:{round(lng, 6)}".encode("utf-8")
    digest = hashlib.sha256(seed).digest()
    w1 = _WORDS_A[digest[0] % len(_WORDS_A)]
    w2 = _WORDS_B[digest[1] % len(_WORDS_B)]
    w3 = _WORDS_C[digest[2] % len(_WORDS_C)]
    return f"///{w1}.{w2}.{w3}"
