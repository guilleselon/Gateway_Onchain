"""
Gateway HTTP routes.

- Public landing page.
- Internal API for merchants (X-API-Key).
- Public payment UI.
- Merchant login and dashboard.
- Health check.
"""

import hmac
import hashlib
from datetime import datetime, timezone

from eth_utils import is_checksum_address
from fasthtml.common import JSONResponse, Response, RedirectResponse
from starlette.requests import Request
from web3 import Web3

from app.config import setup_logging
from app.core import payments
from app.core.auth import (
    authenticate_api_key,
    get_current_merchant,
    login_session,
    logout_session,
)
from app.core.db import get_session
from app.core.models import Chain, Payment, PaymentStatus, Token
from app.templates import (
    _QRContent,
    dashboard_home,
    dashboard_payment_detail,
    dashboard_payments,
    dashboard_settings,
    error_layout,
    landing_page,
    login_page,
    payment_layout,
    status_fragment,
)

log = setup_logging("gateway_app")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _payment_to_dict(p: Payment) -> dict:
    return {
        "payment_id": p.id,
        "external_ref": p.external_ref,
        "merchant_name": p.merchant_name,
        "status": p.status,
        "token_id": p.token_id,
        "wallet_address": p.wallet_address,
        "proxy_address": p.proxy_address,
        "webhook_url": p.webhook_url,
        "amount_usd_cents_expected": p.amount_usd_cents_expected,
        "amount_token_suggested": p.amount_token_suggested,
        "amount_token_received": p.amount_token_received,
        "amount_usd_cents_received": p.amount_usd_cents_received,
        "rate_used": str(p.rate_used) if p.rate_used else None,
        "tx_hash": p.tx_hash,
        "block_number": p.block_number,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "expires_at": p.expires_at.isoformat() if p.expires_at else None,
        "confirmed_at": p.confirmed_at.isoformat() if p.confirmed_at else None,
    }


def _chain_for_token(s, token) -> Chain | None:
    if not token:
        return None
    return s.get(Chain, token.chain_id)


def _validate_wallet(wallet: str) -> str | None:
    if not wallet or not isinstance(wallet, str):
        return None
    try:
        if not is_checksum_address(wallet):
            wallet = Web3.to_checksum_address(wallet)
        return wallet
    except Exception:
        return None


def _validate_webhook_url(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False
    return url.startswith(("http://", "https://"))


def _derive_webhook_secret(api_key: str) -> str:
    return hmac.new(
        api_key.encode(), b"webhook-secret", hashlib.sha256
    ).hexdigest()


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------

def register(app, rt) -> None:

    # -----------------------------------------------------------------------
    # Landing
    # -----------------------------------------------------------------------

    @rt("/", methods=["GET"])
    def home(request: Request):
        return landing_page()

    # -----------------------------------------------------------------------
    # Health
    # -----------------------------------------------------------------------

    @rt("/health", methods=["GET"])
    def health():
        return JSONResponse({
            "status": "ok",
            "ts": datetime.now(timezone.utc).isoformat(),
        })

    # -----------------------------------------------------------------------
    # Login / logout
    # -----------------------------------------------------------------------

    @rt("/login", methods=["GET"])
    def login_get(request: Request):
        if get_current_merchant(request):
            return RedirectResponse("/dashboard", status_code=302)
        return login_page()

    @rt("/login", methods=["POST"])
    async def login_post(request: Request):
        form = await request.form()
        api_key = (form.get("api_key") or "").strip()

        print(f"[LOGIN] POST received, len={len(api_key)} "
              f"prefix={api_key[:16]!r}")

        from app.core.auth import hash_api_key
        h = hash_api_key(api_key) if api_key else ""

        from app.core.models import ApiKey
        with get_session() as s:
            all_keys = s.query(ApiKey).all()
            match = None
            for k in all_keys:
                if k.key_hash == h and k.active:
                    match = k
                    break

        print(f"[LOGIN] keys_in_db={len(all_keys)} match={bool(match)}")

        if not match:
            return login_page(
                error=f"Invalid API key. (len={len(api_key)}, "
                      f"keys in DB={len(all_keys)})"
            )

        merchant = {
            "id": match.id,
            "name": match.name,
            "fee_bps_default": match.fee_bps_default,
        }
        login_session(request, merchant["id"])
        print(f"[LOGIN] session created for id={merchant['id']}")
        return RedirectResponse("/dashboard", status_code=302)

    @rt("/logout", methods=["GET", "POST"])
    async def logout(request: Request):
        logout_session(request)
        return RedirectResponse("/login", status_code=302)

    # -----------------------------------------------------------------------
    # Dashboard
    # -----------------------------------------------------------------------

    @rt("/dashboard", methods=["GET"])
    def dashboard(request: Request):
        merchant = get_current_merchant(request)
        if not merchant:
            return RedirectResponse("/login", status_code=302)

        with get_session() as s:
            payments_list = (
                s.query(Payment)
                .filter_by(api_key_id=merchant["id"])
                .order_by(Payment.id.desc())
                .all()
            )
            total = len(payments_list)
            confirmed = sum(
                1 for p in payments_list
                if p.status in PaymentStatus.TERMINAL_OK
            )
            pending = sum(
                1 for p in payments_list
                if p.status in (PaymentStatus.PENDING,
                                PaymentStatus.USER_CLAIMED,
                                PaymentStatus.DETECTED,
                                PaymentStatus.DEPLOYING)
            )
            usd_received = sum(
                (p.amount_usd_cents_received or 0) for p in payments_list
                if p.status in PaymentStatus.TERMINAL_OK
            ) / 100

        stats = {
            "total": total,
            "confirmed": confirmed,
            "pending": pending,
            "usd_received": usd_received,
        }
        return dashboard_home(merchant, stats)

    @rt("/dashboard/payments", methods=["GET"])
    def dashboard_payments_route(request: Request):
        merchant = get_current_merchant(request)
        if not merchant:
            return RedirectResponse("/login", status_code=302)

        with get_session() as s:
            payments_list = (
                s.query(Payment)
                .filter_by(api_key_id=merchant["id"])
                .order_by(Payment.id.desc())
                .limit(200)
                .all()
            )
        return dashboard_payments(merchant, payments_list)

    @rt("/dashboard/payments/{payment_id}", methods=["GET"])
    def dashboard_payment_detail_route(request: Request, payment_id: int):
        merchant = get_current_merchant(request)
        if not merchant:
            return RedirectResponse("/login", status_code=302)

        with get_session() as s:
            p = payments.get_payment(s, payment_id)
            if not p or p.api_key_id != merchant["id"]:
                return RedirectResponse("/dashboard/payments", status_code=302)
            token = s.get(Token, p.token_id)
            chain = _chain_for_token(s, token)
        return dashboard_payment_detail(merchant, p, token, chain)

    @rt("/dashboard/settings", methods=["GET"])
    def dashboard_settings_route(request: Request):
        merchant = get_current_merchant(request)
        if not merchant:
            return RedirectResponse("/login", status_code=302)
        return dashboard_settings(merchant)

    # -----------------------------------------------------------------------
    # Internal API
    # -----------------------------------------------------------------------

    @rt("/api/payments", methods=["POST"])
    async def api_create_payment(request: Request):
        api_key = request.headers.get("x-api-key")
        merchant = authenticate_api_key(api_key)
        if not merchant:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid json"}, status_code=400)

        external_ref = body.get("external_ref")
        amount_usd_cents = body.get("amount_usd_cents")
        token_id = body.get("token_id")
        wallet_raw = body.get("wallet_address")
        webhook_url = body.get("webhook_url")
        webhook_secret = body.get("webhook_secret")
        merchant_name = body.get("merchant_name")

        missing = []
        if not external_ref:
            missing.append("external_ref")
        if not amount_usd_cents:
            missing.append("amount_usd_cents")
        if not token_id:
            missing.append("token_id")
        if not wallet_raw:
            missing.append("wallet_address")
        if not webhook_url:
            missing.append("webhook_url")
        if missing:
            return JSONResponse(
                {"error": f"missing fields: {', '.join(missing)}"},
                status_code=400,
            )

        wallet_address = _validate_wallet(wallet_raw)
        if not wallet_address:
            return JSONResponse({"error": "invalid wallet_address"},
                                status_code=400)

        if not _validate_webhook_url(webhook_url):
            return JSONResponse(
                {"error": "webhook_url must start with http:// or https://"},
                status_code=400,
            )

        try:
            amount_usd_cents = int(amount_usd_cents)
            token_id = int(token_id)
        except (ValueError, TypeError):
            return JSONResponse(
                {"error": "amount_usd_cents and token_id must be integers"},
                status_code=400,
            )

        if not webhook_secret:
            webhook_secret = _derive_webhook_secret(api_key)

        try:
            with get_session() as s:
                p = payments.create_payment(
                    s,
                    external_ref=external_ref,
                    amount_usd_cents=amount_usd_cents,
                    token_id=token_id,
                    wallet_address=wallet_address,
                    webhook_url=webhook_url,
                    webhook_secret=webhook_secret,
                    api_key_id=merchant["id"],
                    merchant_name=merchant_name,
                )
                result = _payment_to_dict(p)
        except payments.PaymentError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        except Exception as e:
            log.exception("error creating payment")
            return JSONResponse({"error": f"internal: {e}"}, status_code=500)

        return JSONResponse(result, status_code=201)

    @rt("/api/payments/{payment_id}", methods=["GET"])
    def api_get_payment(request: Request, payment_id: int):
        api_key = request.headers.get("x-api-key")
        merchant = authenticate_api_key(api_key)
        if not merchant:
            return JSONResponse({"error": "unauthorized"}, status_code=401)

        with get_session() as s:
            p = payments.get_payment(s, payment_id)
            if not p or p.api_key_id != merchant["id"]:
                return JSONResponse({"error": "not found"}, status_code=404)
            return JSONResponse(_payment_to_dict(p))

    # -----------------------------------------------------------------------
    # Public payment UI
    # -----------------------------------------------------------------------

    @rt("/pay/{payment_id}", methods=["GET"])
    def pay_page(request: Request, payment_id: int):
        with get_session() as s:
            p = payments.get_payment(s, payment_id)
            if not p:
                return error_layout("The link is invalid or the payment was removed.")
            token = s.get(Token, p.token_id)
            if not token:
                return error_layout("Payment data is incomplete.")
            chain = _chain_for_token(s, token)
            merchant_dict = {"name": p.merchant_name or "Gateway"}
            return payment_layout(p, token, chain, merchant_dict)

    @rt("/pay/{payment_id}/qr", methods=["GET"])
    def pay_qr(request: Request, payment_id: int):
        with get_session() as s:
            p = payments.get_payment(s, payment_id)
            if not p:
                return Response("", status_code=404)
            token = s.get(Token, p.token_id)
            if not token:
                return Response("", status_code=404)
            chain = _chain_for_token(s, token)
            return _QRContent(
                p, token,
                chain_name=chain.name if chain else "Unknown",
                chain_id=chain.chain_id if chain else 0,
                explorer_url=getattr(chain, "explorer_url", None) if chain else None,
            )

    @rt("/pay/{payment_id}/status", methods=["GET"])
    def pay_status(request: Request, payment_id: int):
        with get_session() as s:
            p = payments.get_payment(s, payment_id)
            if not p:
                return Response("", status_code=404)
            return status_fragment(p)

    @rt("/pay/{payment_id}/sent", methods=["POST"])
    def pay_sent(request: Request, payment_id: int):
        with get_session() as s:
            p = payments.get_payment(s, payment_id)
            if not p:
                return Response("", status_code=404)
            if p.status in (PaymentStatus.PENDING, PaymentStatus.USER_CLAIMED):
                payments.mark_user_claimed(s, p)
                p = payments.get_payment(s, payment_id)
            return status_fragment(p)

