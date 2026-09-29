"""
Issue an API key for a merchant.

Usage:
    python scripts/create_api_key.py --name "My Merchant"
    python scripts/create_api_key.py --name "My Merchant" --fee-bps 100
    python scripts/create_api_key.py --list
    python scripts/create_api_key.py --deactivate 3

The plain key is shown ONLY ONCE. Save it before closing the terminal.
Only its SHA-256 hash and a short preview are stored in the database.
"""

import argparse
import hashlib
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import setup_logging
from app.core.db import get_session, init_db
from app.core.models import ApiKey

log = setup_logging("gateway_create_api_key")

API_KEY_PREFIX = "sk_live_"
PREVIEW_LEN = 16


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _generate_key() -> str:
    """sk_live_ + 64 hex chars = 72 characters."""
    return API_KEY_PREFIX + secrets.token_hex(32)


def _preview(key: str) -> str:
    """First 16 chars + '...' for listing purposes."""
    return key[:PREVIEW_LEN] + "..."


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_create(name: str, fee_bps: int | None) -> int:
    plain_key = _generate_key()
    key_hash = _hash(plain_key)
    key_preview = _preview(plain_key)

    init_db()
    with get_session() as s:
        # Avoid collisions (extremely unlikely)
        while s.query(ApiKey).filter_by(key_hash=key_hash).one_or_none():
            plain_key = _generate_key()
            key_hash = _hash(plain_key)
            key_preview = _preview(plain_key)

        k = ApiKey(
            name=name,
            key_hash=key_hash,
            key_preview=key_preview,
            fee_bps_default=fee_bps,
            active=True,
        )
        s.add(k)
        s.flush()
        key_id = k.id

    print()
    print("=" * 70)
    print(f"  API key created for: {name}")
    print(f"  DB id: {key_id}")
    print(f"  preview: {key_preview}")
    if fee_bps is not None:
        print(f"  fee_bps_default: {fee_bps} ({fee_bps / 100:.2f}%)")
    print("=" * 70)
    print()
    print("  " + plain_key)
    print()
    print("=" * 70)
    print("  COPY IT NOW. It will not be shown again.")
    print("  If you lose it, issue a new one and deactivate this one.")
    print("=" * 70)
    print()
    return 0


def cmd_list() -> int:
    init_db()
    with get_session() as s:
        keys = s.query(ApiKey).order_by(ApiKey.id).all()
        if not keys:
            print("No API keys issued.")
            return 0
        print(f"{'ID':<5} {'Name':<25} {'Preview':<22} {'Active':<8} "
              f"{'Fee bps':<10} {'Last use':<25}")
        print("-" * 100)
        for k in keys:
            last = (k.last_used_at.strftime("%Y-%m-%d %H:%M:%S")
                    if k.last_used_at else "never")
            fee = str(k.fee_bps_default) if k.fee_bps_default is not None else "-"
            active = "yes" if k.active else "no"
            preview = k.key_preview or "-"
            print(f"{k.id:<5} {k.name[:23]:<25} {preview:<22} {active:<8} "
                  f"{fee:<10} {last:<25}")
    return 0


def cmd_deactivate(key_id: int) -> int:
    init_db()
    with get_session() as s:
        k = s.get(ApiKey, key_id)
        if not k:
            print(f"No API key found with id={key_id}")
            return 1
        k.active = False
        print(f"API key id={key_id} ({k.name}) deactivated.")
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Issue and manage merchant API keys."
    )
    parser.add_argument("--name", type=str, help="Merchant name")
    parser.add_argument("--fee-bps", type=int, default=None,
                        help="Default fee in basis points (100 = 1%%)")
    parser.add_argument("--list", action="store_true",
                        help="List all issued API keys")
    parser.add_argument("--deactivate", type=int, metavar="ID",
                        help="Deactivate the API key with this id")
    args = parser.parse_args()

    if args.list:
        return cmd_list()

    if args.deactivate is not None:
        return cmd_deactivate(args.deactivate)

    if not args.name:
        parser.print_help()
        return 1

    if args.fee_bps is not None:
        if args.fee_bps < 0 or args.fee_bps > 500:
            print("fee_bps must be between 0 and 500 (max 5%).")
            return 1

    return cmd_create(args.name, args.fee_bps)


if __name__ == "__main__":
    sys.exit(main())

