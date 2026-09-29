"""
Create the gateway tables and seed initial data:
- One chain, from .env.
- Native and common tokens for that chain.

Does not create merchants: merchants authenticate with API keys issued
by scripts/create_api_key.py.

Idempotent: running it multiple times does not duplicate data.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import (
    CHAIN_CONFIRMATIONS,
    CHAIN_EXPLORER,
    CHAIN_ID,
    CHAIN_NAME,
    FACTORY_ADDRESS,
    MASTER_ADDRESS,
    RPC_URL,
    setup_logging,
)
from app.core.db import get_session, init_db
from app.core.models import Chain, Token

log = setup_logging("gateway_init_db")


def main() -> None:
    log.info("Creating gateway tables...")
    init_db()
    log.info("Tables created.")

    with get_session() as s:
        # --- Chain ---
        chain = s.query(Chain).filter_by(chain_id=CHAIN_ID).one_or_none()
        if not chain:
            chain = Chain(
                name=CHAIN_NAME,
                chain_id=CHAIN_ID,
                rpc_url=RPC_URL,
                factory_address=FACTORY_ADDRESS,
                master_address=MASTER_ADDRESS,
                explorer_url=CHAIN_EXPLORER or None,
                confirmations_required=CHAIN_CONFIRMATIONS,
                active=True,
            )
            s.add(chain)
            s.flush()
            log.info(
                f"Chain '{CHAIN_NAME}' (id={chain.id}, "
                f"confirmations={CHAIN_CONFIRMATIONS}) inserted."
            )
        else:
            # Keep existing chain but refresh mutable fields from .env
            chain.name = CHAIN_NAME
            chain.rpc_url = RPC_URL
            chain.factory_address = FACTORY_ADDRESS
            chain.master_address = MASTER_ADDRESS
            chain.explorer_url = CHAIN_EXPLORER or None
            chain.confirmations_required = CHAIN_CONFIRMATIONS
            log.info(
                f"Chain '{CHAIN_NAME}' already exists "
                f"(id={chain.id}). Fields refreshed from .env."
            )

        # --- Tokens ---
        tokens_initial = [
            {
                "symbol": "MON",
                "address": None,
                "decimals": 18,
                "coingecko_id": "monad",
            },
            {
                "symbol": "USDC",
                "address": None,     # fill in once an ERC-20 is deployed
                "decimals": 6,
                "coingecko_id": "usd-coin",
            },
        ]
        for t in tokens_initial:
            existing = (
                s.query(Token)
                .filter_by(chain_id=chain.id, symbol=t["symbol"])
                .one_or_none()
            )
            if existing:
                if existing.coingecko_id != t["coingecko_id"]:
                    existing.coingecko_id = t["coingecko_id"]
                    log.info(f"Token '{t['symbol']}' coingecko_id updated.")
                else:
                    log.info(f"Token '{t['symbol']}' already exists.")
                continue
            s.add(Token(chain_id=chain.id, active=True, **t))
            log.info(f"Token '{t['symbol']}' inserted.")

    log.info("init_db finished.")
    log.info("")
    log.info("To issue an API key:")
    log.info('  python scripts/create_api_key.py --name "My Merchant"')


if __name__ == "__main__":
    main()

