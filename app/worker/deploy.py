"""
Proxy deployment task for the gateway worker.

Two phases per cycle:

1. Send phase: for every detected payment, send the deploy transaction
   and mark the payment as deploying. Does NOT wait for the receipt.
   Uses a local nonce manager to avoid collisions when multiple txs
   are sent in the same cycle.

2. Confirm phase: for every deploying payment, check if its deploy tx
   has been confirmed, verify the proxy was actually deployed at the
   expected address, and that the balance was forwarded to the merchant.
   Only then mark it as confirmed.
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
    """
    Check deploying payments for confirmations.

    Beyond checking receipt.status == 1, verifies:
      1. The proxy at payment.proxy_address actually has code.
      2. The proxy's balance is 0 (funds were forwarded).

    If either check fails, marks the payment as FAILED with a clear reason
    instead of falsely reporting CONFIRMED.
    """
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
            continue

        if receipt.status != 1:
            payments.mark_failed(session, p, "deploy tx reverted")
            continue

        # --- Post-deploy verification ---

        # 1. Does the expected proxy address actually have code?
        try:
            proxy_has_code = blockchain.has_code(w3, p.proxy_address)
        except Exception as e:
            log.warning(
                f"payment {p.id}: could not check proxy code: {e}"
            )
            continue

        if not proxy_has_code:
            payments.mark_failed(
                session, p,
                f"deploy tx succeeded but no code at expected proxy "
                f"{p.proxy_address} (CREATE2 address mismatch — "
                f"check that calculate_proxy uses the same msg.sender)"
            )
            log.error(
                f"payment {p.id}: CREATE2 MISMATCH. Expected proxy at "
                f"{p.proxy_address} but the factory deployed elsewhere. "
                f"User funds may be trapped in {p.proxy_address}."
            )
            continue

        # 2. Was the balance forwarded to the merchant?
        try:
            remaining = blockchain.get_balance(w3, p.proxy_address)
        except Exception as e:
            log.warning(
                f"payment {p.id}: could not read proxy balance: {e}"
            )
            continue

        if remaining > 0:
            log.warning(
                f"payment {p.id}: proxy {p.proxy_address} still holds "
                f"{remaining} wei after initialize. Not marking as confirmed."
            )
            # Leave it in DEPLOYING so it gets re-checked. If it stays
            # stuck, an operator must intervene.
            continue

        payments.mark_confirmed(
            session, p, p.deploy_tx_hash, receipt.blockNumber
        )
        confirmed += 1
        log.info(
            f"payment {p.id}: confirmed in block {receipt.blockNumber} "
            f"(proxy {p.proxy_address} empty)"
        )

    return confirmed


# ---------------------------------------------------------------------------
# Cycle entry
# ---------------------------------------------------------------------------

def run_once() -> int:
    """Run one deploy cycle. Returns payments that moved forward."""
    with get_session() as s:
        sent = _send_phase(s)
        confirmed = _confirm_phase(s)
        return sent + confirmed
