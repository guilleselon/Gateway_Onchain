"""
USD/token exchange rate retrieval.

Primary provider: CoinMarketCap (Pro API).
Fallback provider: CoinGecko (used if CMC fails or is not configured).

Token symbols are used to look up CMC quotes (e.g. "MON", "USDC").
CoinGecko IDs are used as a fallback (e.g. "monad", "usd-coin").

Functions:
- fetch_rate_cmc(): query CoinMarketCap and return a Decimal.
- fetch_rate_coingecko(): query CoinGecko and return a Decimal.
- fetch_rate(): try CMC first, then CoinGecko.
- save_rate(): insert a new row in exchange_rates.
- get_latest_rate(): return the most recent rate for a token.
- update_all_rates(): iterate over active tokens and refresh their rates.
"""

from decimal import Decimal

import httpx
from sqlalchemy.orm import Session

from app.config import (
    COINMARKETCAP_API_KEY,
    COINGECKO_API_KEY,
    setup_logging,
)
from app.core.db import get_session
from app.core.models import ExchangeRate, Token

log = setup_logging("gateway_rates")

CMC_URL = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"


# ---------------------------------------------------------------------------
# CoinMarketCap
# ---------------------------------------------------------------------------

def fetch_rate_cmc(symbol: str) -> Decimal:
    """
    Query CoinMarketCap Pro API for the token's USD price.

    Args:
        symbol: ticker symbol (e.g. "MON", "USDC").

    Returns:
        Decimal with the price in USD.

    Raises:
        ValueError if the key is missing or the symbol is not found.
    """
    if not COINMARKETCAP_API_KEY:
        raise ValueError("COINMARKETCAP_API_KEY is not set")

    headers = {
        "X-CMC_PRO_API_KEY": COINMARKETCAP_API_KEY,
        "Accept": "application/json",
    }
    params = {"symbol": symbol.upper(), "convert": "USD"}

    with httpx.Client(timeout=15.0) as client:
        r = client.get(CMC_URL, headers=headers, params=params)
        r.raise_for_status()
        data = r.json()

    # CoinMarketCap wraps results in {"status": ..., "data": {...}}
    status = data.get("status", {})
    if status.get("error_code", 0) != 0:
        raise ValueError(
            f"CMC error {status.get('error_code')}: "
            f"{status.get('error_message')}"
        )

    entries = data.get("data", {})
    entry = entries.get(symbol.upper())
    if not entry:
        raise ValueError(f"CMC did not return data for {symbol}")

    price = entry["quote"]["USD"]["price"]
    return Decimal(str(price))


# ---------------------------------------------------------------------------
# CoinGecko (fallback)
# ---------------------------------------------------------------------------

def fetch_rate_coingecko(coingecko_id: str) -> Decimal:
    """Query CoinGecko and return the token's USD price."""
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
# Unified fetch with fallback
# ---------------------------------------------------------------------------

def fetch_rate(token: Token) -> tuple[Decimal, str]:
    """
    Try to fetch a USD rate for the token.

    Order:
      1. CoinMarketCap (uses token.symbol)
      2. CoinGecko (uses token.coingecko_id, if set)

    Returns:
        (usd_rate, source_name)

    Raises:
        ValueError if all providers fail.
    """
    errors = []

    # 1. CoinMarketCap
    if COINMARKETCAP_API_KEY:
        try:
            rate = fetch_rate_cmc(token.symbol)
            return rate, "cmc"
        except Exception as e:
            errors.append(f"cmc: {e}")
            log.debug(f"CMC failed for {token.symbol}: {e}")

    # 2. CoinGecko
    if token.coingecko_id:
        try:
            rate = fetch_rate_coingecko(token.coingecko_id)
            return rate, "coingecko"
        except Exception as e:
            errors.append(f"coingecko: {e}")
            log.debug(f"CoinGecko failed for {token.symbol}: {e}")

    raise ValueError(
        f"all providers failed for {token.symbol}: {'; '.join(errors)}"
    )


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
    Iterate over active tokens and refresh their rates.

    Uses CMC as primary and CoinGecko as fallback. If no provider is
    configured for a token, it is skipped.
    """
    updated = 0
    with get_session() as s:
        tokens = (
            s.query(Token)
            .filter(Token.active.is_(True))
            .all()
        )

        for token in tokens:
            # Skip if no provider is configured
            has_cmc = bool(COINMARKETCAP_API_KEY)
            has_cg = bool(token.coingecko_id)
            if not has_cmc and not has_cg:
                log.debug(f"{token.symbol}: no provider configured, skipping")
                continue

            try:
                rate, source = fetch_rate(token)
                save_rate(s, token.id, rate, source)
                updated += 1
                log.info(f"{token.symbol}: {rate} USD ({source})")
            except Exception as e:
                log.warning(f"Could not update {token.symbol}: {e}")

    return updated
