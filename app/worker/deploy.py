"""
Proxy deployment task for the gateway worker.

Two phases per cycle:

1. Send phase: for every detected payment, send the deploy transaction
   and mark the payment as deploying. Does NOT wait for the receipt.
   Uses a local nonce manager to avoid collisions when multiple txs
   are sent in the same cycle.

2. Confirm phase: for every deploying payment, check if its deploy tx
   has been confirmed. If so, mark it as confirmed (or failed).
"""

from app.config import (
    DAILY_DEPLOY_QUOTA,
    MIN_GATEWAY_BALANCE_WEI,
    PRIVATE_KEY,
    get_factory_contract,
    get_web3,
    setup_logging,
)
from app.core import blockchain, payments
from app.core.db import get_session
from app.core.models import Chain, Payment, PaymentStatus, Token

log = setup_logging("gateway_worker")


def _gateway_balance_ok(w3) -> tuple[bool, int]:
    """Check the gateway balance. Returns (ok, balance_wei)."""
    account = w3.eth.account.from_key(PRIVATE_KEY)
    balance = w3.eth.get_balance(account.address)
    return balance >= MIN_GATEWAY_BALANCE_WEI, balance


# ---------------------------------------------------------------------------
# Phase 1: send deploy transactions
# ---------------------------------------------------------------------------

def _send_phase(session) -> int:
    """Send deploy txs for detected payments. Returns how many were sent."""
    pending = payments.get_payments_to_deploy(session)
    if not pending:
        return 0

    # Balance check (once per cycle)
    first_token = session.get(Token, pending[0].token_id)
    first_chain = session.get(Chain, first_token.chain_id) if first_token else None
    if first_chain:
        w3_check = get_web3(first_chain.rpc_url)
        ok, balance = _gateway_balance_ok(w3_check)
        if not ok:
            log.critical(
                f"Gateway balance below minimum "
                f"({w3_check.from_wei(balance, 'ether')} native). "
                f"Not sending new deploys."
            )
            return 0

    sent = 0
    for p in pending:
        token = session.get(Token, p.token_id)
        chain = session.get(Chain, token.chain_id) if token else None
        if not (token and chain):
            log.warning(f"payment {p.id}: incomplete data, skipping")
            continue

        # Daily quota per merchant
        if p.api_key_id is not None:
            confirmed_today = payments.count_confirmed_today(session, p.api_key_id)
            if confirmed_today >= DAILY_DEPLOY_QUOTA:
                log.warning(
                    f"payment {p.id}: merchant {p.api_key_id} exceeded "
                    f"daily quota ({confirmed_today}/{DAILY_DEPLOY_QUOTA})"
                )
                payments.mark_failed(
                    session, p, f"daily quota exceeded ({DAILY_DEPLOY_QUOTA})"
                )
                continue

        w3 = get_web3(chain.rpc_url)
        factory = get_factory_contract(w3, chain.factory_address)

        try:
            tx_hash = blockchain.deploy_proxy(
                w3, factory, p.order_id, p.wallet_address,
                p.salt, PRIVATE_KEY,
            )
            payments.mark_deploying(session, p, tx_hash)
            sent += 1
            log.info(f"payment {p.id}: deploy tx sent {tx_hash[:16]}...")
        except Exception as e:
            log.exception(f"payment {p.id}: exception sending deploy")
            payments.mark_failed(session, p, f"send exception: {e}")

    return sent


# ---------------------------------------------------------------------------
# Phase 2: check confirmations
# ---------------------------------------------------------------------------

def _confirm_phase(session) -> int:
    """Check deploying payments for confirmations. Returns how many confirmed."""
    deploying = (
        session.query(Payment)
        .filter(Payment.status == PaymentStatus.DEPLOYING)
        .all()
    )
    if not deploying:
        return 0

    confirmed = 0
    for p in deploying:
        if not p.deploy_tx_hash:
            continue
        token = session.get(Token, p.token_id)
        chain = session.get(Chain, token.chain_id) if token else None
        if not chain:
            continue

        w3 = get_web3(chain.rpc_url)
        try:
            receipt = w3.eth.get_transaction_receipt(p.deploy_tx_hash)
        except Exception:
            receipt = None

        if receipt is None:
            # Still pending; check again next cycle
            continue

        if receipt.status == 1:
            payments.mark_confirmed(
                session, p, p.deploy_tx_hash, receipt.blockNumber
            )
            confirmed += 1
            log.info(
                f"payment {p.id}: confirmed in block {receipt.blockNumber}"
            )
        else:
            payments.mark_failed(session, p, "deploy tx reverted")

    return confirmed


# ---------------------------------------------------------------------------
# Cycle entry
# ---------------------------------------------------------------------------

def run_once() -> int:
    """
    Run one deploy cycle. Returns the total number of payments that
    moved forward (sent + confirmed).
    """
    with get_session() as s:
        sent = _send_phase(s)
        confirmed = _confirm_phase(s)
        return sent + confirmed

