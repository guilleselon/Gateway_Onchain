
"""
Inspect the gateway database.

Usage:
    python scripts/inspect_db.py                       # summary
    python scripts/inspect_db.py --tables              # list tables
    python scripts/inspect_db.py --payments            # last 20 payments
    python scripts/inspect_db.py --payments 5          # last 5 payments
    python scripts/inspect_db.py --payments --api-key 3
    python scripts/inspect_db.py --payment 3           # payment detail
    python scripts/inspect_db.py --merchant 1          # merchant detail
    python scripts/inspect_db.py --api-keys            # list API keys
    python scripts/inspect_db.py --chains              # chains and tokens
    python scripts/inspect_db.py --rates               # latest rates
    python scripts/inspect_db.py --webhooks            # webhook attempts
    python scripts/inspect_db.py --sql "SELECT ..."    # raw SQL (SELECT)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, text

from app.core.db import engine, get_session
from app.core.models import (
    ApiKey, Chain, ExchangeRate, Payment, PaymentEvent, Token,
    WebhookAttempt,
)


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------

def _line(char: str = "─", width: int = 90) -> None:
    print(char * width)


def _title(text: str) -> None:
    print()
    print(f"▸ {text}")
    _line()


def _fmt_date(dt) -> str:
    if dt is None:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _fmt_cents(cents) -> str:
    if cents is None:
        return "—"
    return f"${cents / 100:.2f}"


def _shorten(addr: str | None, n: int = 10) -> str:
    if not addr:
        return "—"
    if len(addr) <= n * 2 + 3:
        return addr
    return f"{addr[:n]}...{addr[-6:]}"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_summary() -> int:
    _title("Database summary")
    print(f"  URL: {engine.url}")
    print()

    with get_session() as s:
        n_chains = s.query(Chain).count()
        n_tokens = s.query(Token).count()
        n_api_keys = s.query(ApiKey).count()
        n_api_keys_active = s.query(ApiKey).filter(ApiKey.active.is_(True)).count()
        n_payments = s.query(Payment).count()
        n_rates = s.query(ExchangeRate).count()
        n_events = s.query(PaymentEvent).count()
        n_webhook_attempts = s.query(WebhookAttempt).count()

        statuses = s.query(Payment.status).all()
        status_counts: dict[str, int] = {}
        for (status,) in statuses:
            status_counts[status] = status_counts.get(status, 0) + 1

    print(f"  Chains:             {n_chains}")
    print(f"  Tokens:             {n_tokens}")
    print(f"  API keys:           {n_api_keys} ({n_api_keys_active} active)")
    print(f"  Payments:           {n_payments}")
    print(f"  Exchange rates:     {n_rates}")
    print(f"  Payment events:     {n_events}")
    print(f"  Webhook attempts:   {n_webhook_attempts}")
    print()

    if status_counts:
        print("  Payments by status:")
        for status, n in sorted(status_counts.items()):
            print(f"    {status:<20} {n}")
    print()
    return 0


def cmd_tables() -> int:
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    _title(f"Tables ({len(tables)})")
    for t in sorted(tables):
        cols = inspector.get_columns(t)
        print(f"  {t:<25} ({len(cols)} columns)")
    print()
    return 0


def cmd_payments(limit: int | None, api_key_id: int | None) -> int:
    label = "Payments"
    if api_key_id is not None:
        label += f" (api_key_id={api_key_id})"
    _title(label)

    with get_session() as s:
        q = s.query(Payment).order_by(Payment.id.desc())
        if api_key_id is not None:
            q = q.filter(Payment.api_key_id == api_key_id)
        if limit:
            q = q.limit(limit)
        payments = q.all()

        if not payments:
            print("  (no payments)")
            print()
            return 0

        print(f"  {'ID':<4} {'Ref':<22} {'Status':<12} "
              f"{'USD':<9} {'Token':<6} {'Proxy':<16} {'Created':<19}")
        _line(width=105)

        for p in payments:
            token = s.get(Token, p.token_id)
            symbol = token.symbol if token else "?"
            print(f"  {p.id:<4} "
                  f"{(p.external_ref or '')[:20]:<22} "
                  f"{p.status:<12} "
                  f"{_fmt_cents(p.amount_usd_cents_expected):<9} "
                  f"{symbol:<6} "
                  f"{_shorten(p.proxy_address, 6):<16} "
                  f"{_fmt_date(p.created_at):<19}")

        print()
        print(f"  Total: {len(payments)} payment(s)")
    print()
    return 0


def cmd_payment(payment_id: int) -> int:
    _title(f"Payment detail id={payment_id}")
    with get_session() as s:
        p = s.get(Payment, payment_id)
        if not p:
            print(f"  No payment with id={payment_id}")
            return 1

        token = s.get(Token, p.token_id)
        chain = s.get(Chain, token.chain_id) if token else None

        merchant = "—"
        if p.api_key_id:
            k = s.get(ApiKey, p.api_key_id)
            if k:
                merchant = f"{k.name} (api_key_id={k.id})"

        def row(k: str, v) -> None:
            print(f"  {k:<28} {v}")

        row("ID", p.id)
        row("external_ref", p.external_ref)
        row("merchant (api_key)", merchant)
        row("merchant_name (optional)", p.merchant_name or "—")
        row("status", p.status)
        _line("·")
        row("token", token.symbol if token else "—")
        row("chain", f"{chain.name} (chain_id={chain.chain_id})" if chain else "—")
        _line("·")
        row("wallet_address (destination)", p.wallet_address)
        row("proxy_address", p.proxy_address)
        row("salt", p.salt[:20] + "..." if p.salt else "—")
        row("order_id", p.order_id)
        _line("·")
        row("amount_usd_cents_expected", _fmt_cents(p.amount_usd_cents_expected))
        row("amount_token_suggested", p.amount_token_suggested or "—")
        row("amount_token_received", p.amount_token_received or "—")
        row("amount_usd_cents_received", _fmt_cents(p.amount_usd_cents_received))
        row("rate_used", str(p.rate_used) if p.rate_used else "—")
        row("rate_source", p.rate_source or "—")
        _line("·")
        row("webhook_url", p.webhook_url)
        row("webhook_secret", _shorten(p.webhook_secret, 8))
        row("tx_hash", p.tx_hash or "—")
        row("block_number", p.block_number or "—")
        row("deploy_tx_hash", p.deploy_tx_hash or "—")
        _line("·")
        row("created_at", _fmt_date(p.created_at))
        row("expires_at", _fmt_date(p.expires_at))
        row("user_claimed_at", _fmt_date(p.user_claimed_at))
        row("detected_at", _fmt_date(p.detected_at))
        row("confirmed_at", _fmt_date(p.confirmed_at))
        row("webhook_sent_at", _fmt_date(p.webhook_sent_at))

        attempts = (
            s.query(WebhookAttempt)
            .filter_by(payment_id=payment_id)
            .order_by(WebhookAttempt.attempt_number)
            .all()
        )
        if attempts:
            _line("·")
            print(f"  Webhook attempts ({len(attempts)}):")
            for w in attempts:
                status = "OK" if w.success else "FAIL"
                print(f"    [{w.attempt_number}] {status} "
                      f"code={w.response_code or '—'} "
                      f"{_fmt_date(w.attempted_at)}")
                if w.response_body:
                    body = w.response_body[:80].replace("\n", " ")
                    print(f"        {body}")

    print()
    return 0


def cmd_merchant(api_key_id: int) -> int:
    with get_session() as s:
        k = s.get(ApiKey, api_key_id)
        if not k:
            print()
            print(f"  No merchant with api_key_id={api_key_id}")
            print()
            return 1

        _title(f"Merchant: {k.name}")

        def row(label: str, v) -> None:
            print(f"  {label:<20} {v}")

        row("api_key_id", k.id)
        row("Name", k.name)
        row("Key (preview)", k.key_preview or "—")
        row("Active", "yes" if k.active else "no")
        row("Fee bps", k.fee_bps_default if k.fee_bps_default is not None else "—")
        row("Created", _fmt_date(k.created_at))
        row("Last use", _fmt_date(k.last_used_at))

        payments = (
            s.query(Payment)
            .filter(Payment.api_key_id == api_key_id)
            .order_by(Payment.id.desc())
            .all()
        )

        print()
        if not payments:
            print("  This merchant has no payments.")
            print()
            return 0

        print(f"  Payments ({len(payments)}):")
        print()
        print(f"  {'ID':<4} {'Ref':<22} {'Status':<12} "
              f"{'USD':<9} {'Token':<6} {'Proxy':<16} {'Created':<19}")
        _line(width=105)

        for p in payments:
            token = s.get(Token, p.token_id)
            symbol = token.symbol if token else "?"
            print(f"  {p.id:<4} "
                  f"{(p.external_ref or '')[:20]:<22} "
                  f"{p.status:<12} "
                  f"{_fmt_cents(p.amount_usd_cents_expected):<9} "
                  f"{symbol:<6} "
                  f"{_shorten(p.proxy_address, 6):<16} "
                  f"{_fmt_date(p.created_at):<19}")

        total_usd_received = sum(
            (p.amount_usd_cents_received or 0) for p in payments
            if p.status in ("confirmed", "late_detected")
        ) / 100

        print()
        print(f"  Total payments:       {len(payments)}")
        print(f"  Confirmed:            "
              f"{sum(1 for p in payments if p.status in ('confirmed', 'late_detected'))}")
        print(f"  USD received:         ${total_usd_received:.2f}")

    print()
    return 0


def cmd_api_keys() -> int:
    _title("API keys")
    with get_session() as s:
        keys = s.query(ApiKey).order_by(ApiKey.id).all()
        if not keys:
            print("  (no API keys)")
            print()
            return 0

        print(f"  {'ID':<5} {'Name':<25} {'Preview':<22} {'Active':<8} "
              f"{'Fee bps':<10} {'Last use':<20}")
        _line(width=105)
        for k in keys:
            active = "yes" if k.active else "no"
            fee = str(k.fee_bps_default) if k.fee_bps_default is not None else "—"
            preview = k.key_preview or "—"
            print(f"  {k.id:<5} "
                  f"{(k.name or '')[:23]:<25} "
                  f"{preview:<22} "
                  f"{active:<8} "
                  f"{fee:<10} "
                  f"{_fmt_date(k.last_used_at):<20}")
    print()
    return 0


def cmd_chains() -> int:
    _title("Chains and tokens")
    with get_session() as s:
        chains = s.query(Chain).order_by(Chain.id).all()
        if not chains:
            print("  (no chains)")
            print()
            return 0

        for c in chains:
            print(f"  ▸ Chain id={c.id}  {c.name}  (chain_id={c.chain_id})")
            print(f"      rpc_url:           {c.rpc_url}")
            print(f"      factory_address:   {c.factory_address}")
            print(f"      master_address:    {c.master_address}")
            print(f"      explorer_url:      {c.explorer_url or '—'}")
            print(f"      confirmations:     {c.confirmations_required}")
            print(f"      active:            {'yes' if c.active else 'no'}")

            tokens = s.query(Token).filter_by(chain_id=c.id).all()
            if tokens:
                print(f"      tokens ({len(tokens)}):")
                for t in tokens:
                    addr = t.address or "native"
                    print(f"        · {t.symbol:<6} dec={t.decimals:<3} "
                          f"addr={_shorten(addr, 8):<20} "
                          f"cg={t.coingecko_id or '—'}")
            print()
    return 0


def cmd_rates() -> int:
    _title("Latest rates per token")
    with get_session() as s:
        tokens = s.query(Token).order_by(Token.id).all()
        if not tokens:
            print("  (no tokens)")
            print()
            return 0

        for t in tokens:
            latest = (
                s.query(ExchangeRate)
                .filter_by(token_id=t.id)
                .order_by(ExchangeRate.fetched_at.desc())
                .first()
            )
            if latest:
                print(f"  {t.symbol:<6}  ${latest.usd_rate}  "
                      f"({latest.source})  {_fmt_date(latest.fetched_at)}")
            else:
                print(f"  {t.symbol:<6}  (no rate)")
    print()
    return 0


def cmd_webhooks(limit: int = 30) -> int:
    _title(f"Last {limit} webhook attempts")
    with get_session() as s:
        attempts = (
            s.query(WebhookAttempt)
            .order_by(WebhookAttempt.attempted_at.desc())
            .limit(limit)
            .all()
        )
        if not attempts:
            print("  (no attempts)")
            print()
            return 0

        print(f"  {'Payment':<10} {'N':<4} {'OK':<6} {'Code':<6} "
              f"{'Date':<20} {'Response':<30}")
        _line(width=100)
        for w in attempts:
            ok_mark = "yes" if w.success else "no"
            body = (w.response_body or "")[:28].replace("\n", " ")
            print(f"  {w.payment_id:<10} {w.attempt_number:<4} {ok_mark:<6} "
                  f"{str(w.response_code or '—'):<6} "
                  f"{_fmt_date(w.attempted_at):<20} "
                  f"{body:<30}")
    print()
    return 0


def cmd_sql(query: str) -> int:
    _title("SQL query")
    q_strip = query.strip().lower()
    if not q_strip.startswith("select"):
        print("  Only SELECT queries are allowed.")
        return 1

    print(f"  {query}")
    print()
    with engine.connect() as conn:
        try:
            result = conn.execute(text(query))
            columns = list(result.keys())
            rows = result.fetchall()
        except Exception as e:
            print(f"  Error: {e}")
            return 1

        if not rows:
            print("  (no results)")
            return 0

        widths = []
        for i, col in enumerate(columns):
            max_val = max((len(str(r[i])) for r in rows), default=0)
            widths.append(max(len(col), min(max_val, 40)))

        header = " | ".join(c[:w].ljust(w) for c, w in zip(columns, widths))
        print("  " + header)
        print("  " + "-" * len(header))

        for r in rows:
            line = " | ".join(
                str(v)[:w].ljust(w) for v, w in zip(r, widths)
            )
            print("  " + line)

        print()
        print(f"  {len(rows)} row(s)")
    print()
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect the gateway database."
    )
    parser.add_argument("--tables", action="store_true")
    parser.add_argument("--payments", nargs="?", const=20, type=int, metavar="N")
    parser.add_argument("--api-key", type=int, metavar="ID",
                        help="Filter --payments by api_key_id (merchant)")
    parser.add_argument("--payment", type=int, metavar="ID")
    parser.add_argument("--merchant", type=int, metavar="ID")
    parser.add_argument("--api-keys", action="store_true")
    parser.add_argument("--chains", action="store_true")
    parser.add_argument("--rates", action="store_true")
    parser.add_argument("--webhooks", nargs="?", const=30, type=int, metavar="N")
    parser.add_argument("--sql", type=str, metavar="QUERY")

    args = parser.parse_args()

    if args.api_key is not None and args.payments is None:
        parser.error("--api-key requires --payments")

    if not any([args.tables, args.payments, args.payment, args.merchant,
                args.api_keys, args.chains, args.rates, args.webhooks,
                args.sql]):
        return cmd_summary()

    if args.tables:
        return cmd_tables()
    if args.merchant is not None:
        return cmd_merchant(args.merchant)
    if args.payment is not None:
        return cmd_payment(args.payment)
    if args.payments is not None:
        return cmd_payments(args.payments, args.api_key)
    if args.api_keys:
        return cmd_api_keys()
    if args.chains:
        return cmd_chains()
    if args.rates:
        return cmd_rates()
    if args.webhooks is not None:
        return cmd_webhooks(args.webhooks)
    if args.sql:
        return cmd_sql(args.sql)

    return 0


if __name__ == "__main__":
    sys.exit(main())
