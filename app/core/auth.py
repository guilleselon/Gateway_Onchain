"""
Merchant authentication.

Two mechanisms:

1. API key in the X-API-Key header (for programmatic calls).
2. Dashboard session: the merchant pastes their API key once, and the key
   id is stored in the signed session cookie for subsequent requests.

In both cases SHA-256 hashes are compared in constant time.
"""

import hashlib
import hmac
from datetime import datetime, timezone

from starlette.requests import Request

from app.core.db import get_session
from app.core.models import ApiKey


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def hash_api_key(key: str) -> str:
    """Return the SHA-256 hex digest of an API key."""
    return hashlib.sha256(key.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def authenticate_api_key(api_key: str | None) -> dict | None:
    """
    Verify a plain API key against the database.

    Returns {id, name, fee_bps_default} if valid and active.
    Returns None if missing, inactive or invalid.
    Updates last_used_at on each successful call.
    """
    if not api_key or not isinstance(api_key, str):
        return None
    h = hash_api_key(api_key)
    with get_session() as s:
        for k in s.query(ApiKey).filter(ApiKey.active.is_(True)).all():
            if hmac.compare_digest(k.key_hash, h):
                k.last_used_at = datetime.now(timezone.utc)
                return {
                    "id": k.id,
                    "name": k.name,
                    "fee_bps_default": k.fee_bps_default,
                }
    return None


# ---------------------------------------------------------------------------
# Dashboard session
# ---------------------------------------------------------------------------

SESSION_KEY = "api_key_id"


def login_session(request: Request, api_key_id: int) -> None:
    """Mark the merchant as logged in inside the session."""
    request.session[SESSION_KEY] = api_key_id


def logout_session(request: Request) -> None:
    """Clear the session."""
    request.session.pop(SESSION_KEY, None)


def get_current_merchant(request: Request) -> dict | None:
    """
    Return the logged-in merchant from the session, or None.

    Revalidates against the database on every request: if the key was
    deactivated, the merchant loses access even with a valid cookie.
    """
    api_key_id = request.session.get(SESSION_KEY)
    if not api_key_id:
        return None
    with get_session() as s:
        k = s.get(ApiKey, api_key_id)
        if not k or not k.active:
            return None
        return {
            "id": k.id,
            "name": k.name,
            "fee_bps_default": k.fee_bps_default,
        }

