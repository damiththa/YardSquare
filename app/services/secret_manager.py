"""
Google Cloud Secret Manager Integration (`app/services/secret_manager.py`).

Ensures zero sensitive values (API keys, signing tokens) are ever hardcoded in source code
or committed to Git.

Resolution Priority for Any Secret:
1. Environment variable injected at runtime (e.g., via Cloud Run `--set-secrets` or untracked local `.env`).
2. Direct lookup in Google Cloud Secret Manager (`projects/{project_id}/secrets/{secret_id}/versions/latest`)
   when `USE_GCP_SECRET_MANAGER=true` and `GCP_PROJECT_ID` is set.
3. Safe empty string or ephemeral runtime token (never a hardcoded static secret).
"""

import os
import secrets
from functools import lru_cache
from typing import Optional


@lru_cache(maxsize=64)
def get_secret(
    env_var_name: str,
    gcp_secret_id: Optional[str] = None,
    project_id: Optional[str] = None,
    generate_ephemeral_if_missing: bool = False,
) -> str:
    """
    Retrieve a sensitive configuration value without hardcoding.
    - First checks `os.environ[env_var_name]`.
    - Second, if `USE_GCP_SECRET_MANAGER` is enabled, queries Google Cloud Secret Manager.
    - Third, if `generate_ephemeral_if_missing=True`, generates a cryptographically random runtime secret.
    """
    val = os.getenv(env_var_name, "").strip()
    if val:
        return val

    use_sm = os.getenv("USE_GCP_SECRET_MANAGER", "false").lower().strip() in ("true", "1", "yes")
    resolved_project = project_id or os.getenv("GCP_PROJECT_ID") or os.getenv("GOOGLE_CLOUD_PROJECT", "")
    secret_name = gcp_secret_id or env_var_name.lower().replace("_", "-")

    if use_sm and resolved_project:
        try:
            from google.cloud import secretmanager

            client = secretmanager.SecretManagerServiceClient()
            resource_name = f"projects/{resolved_project}/secrets/{secret_name}/versions/latest"
            response = client.access_secret_version(request={"name": resource_name})
            payload = response.payload.data.decode("UTF-8").strip()
            if payload:
                return payload
        except Exception:
            pass

    if generate_ephemeral_if_missing:
        return secrets.token_urlsafe(32)

    return ""
