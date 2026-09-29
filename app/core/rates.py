"""
USD/token exchange rate retrieval from CoinGecko.

Functions:
- fetch_rate_coingecko(): query the API and return a Decimal.
- save_rate(): insert a new row in exchange_rates.
- get_latest_rate(): return the most recent rate for a token.
- update_all_rates(): iterate over active tokens and refresh their rates.
"""

from decimal import Decimal

import httpx
from sqlalchemy.orm import Session

from app.config import COINGECKO_API_KEY, setup_logging
from app.core.db import get_session
from app.core.models import ExchangeRate, Token

log = setup_logging("gateway_rates")

COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_rate_coingecko(coingecko_id: str) -> Decimal:
    """
    Query CoinGecko and return the token's USD price.

    The free tier allows ~10-30 req/min. Enough for a refresh every
    5 minutes with a few tokens.
    """
    headers = {}
    if COINGECKO_API_KEY:
        headers["x-cg-demo-api-key"] = COINGECKO_API_KEY

    params = {"ids": coingecko_id, "vs_currencies": "usd"}

    with httpx.Client(timeout=15.0) as client:
        r = client.get(COINGECKO_URL, params=params, headers=headers)
        r.raise_for_status()
        data = r.json()

    if coingecko_id not in data or "usd" not in data[coingecko_id]:
        raise ValueError(f"CoinGecko did not return USD for {coingecko_id}")

    return Decimal(str(data[coingecko_id]["usd"]))


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_rate(session: Session, token_id: int,
              usd_rate: Decimal, source: str) -> ExchangeRate:
    """
    Insert a new row into exchange_rates. Historical rows are never
    overwritten, so the full rate history is preserved.
    """
    rate = ExchangeRate(token_id=token_id, usd_rate=usd_rate, source=source)
    session.add(rate)
    session.flush()
    return rate


def get_latest_rate(session: Session, token_id: int) -> ExchangeRate | None:
    """Return the most recent rate stored for a token, or None."""
    return (
        session.query(ExchangeRate)
        .filter_by(token_id=token_id)
        .order_by(ExchangeRate.fetched_at.desc())
        .first()
    )


# ---------------------------------------------------------------------------
# Bulk update
# ---------------------------------------------------------------------------

def update_all_rates() -> int:
    """
    Iterate over active tokens that have a coingecko_id, query CoinGecko,
    and store the rates. Returns how many were updated.

    Tokens without a coingecko_id are skipped (e.g. manually priced ones).
    """
    updated = 0
    with get_session() as s:
        tokens = (
            s.query(Token)
            .filter(Token.active.is_(True))
            .filter(Token.coingecko_id.isnot(None))
            .all()
        )

        for token in tokens:
            try:
                rate = fetch_rate_coingecko(token.coingecko_id)
                save_rate(s, token.id, rate, "coingecko")
                updated += 1
                log.info(f"{token.symbol}: {rate} USD")
            except Exception as e:
                log.warning(f"Could not update {token.symbol}: {e}")

    return updated

