"""
FastHTML components for the gateway.
"""

import io
from decimal import Decimal

import qrcode
import qrcode.image.svg
from fasthtml.common import (
    A, Button, Code, Div, Form, H1, H2, H3, I, Input, Label, Main, NotStr,
    P, Span, Title,
)

from app.core.models import PaymentStatus


STATUS_LABELS = {
    PaymentStatus.PENDING:       ("Waiting for your payment...", "amber"),
    PaymentStatus.USER_CLAIMED:  ("Verifying your payment...", "blue"),
    PaymentStatus.DETECTED:      ("Payment detected, processing...", "blue"),
    PaymentStatus.DEPLOYING:     ("Confirming payment...", "blue"),
    PaymentStatus.CONFIRMED:     ("Payment received!", "emerald"),
    PaymentStatus.LATE_DETECTED: ("Payment received (late)", "emerald"),
    PaymentStatus.FAILED:        ("Payment failed", "red"),
    PaymentStatus.EXPIRED:       ("Payment expired", "red"),
}


def eip681_uri(address: str, chain_id: int) -> str:
    return address


def generate_qr_svg(data: str, box_size: int = 10, border: int = 2) -> str:
    factory = qrcode.image.svg.SvgPathImage
    img = qrcode.make(data, image_factory=factory,
                      box_size=box_size, border=border)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")


def _fmt_usd_micros(micros) -> str:
    if micros is None or micros == 0:
        return "$0.00"
    m = int(micros)
    if m < 10_000:
        return f"${m / 1_000_000:.6f}"
    if m < 1_000_000:
        return f"${m / 1_000_000:.4f}"
    return f"${m / 1_000_000:.2f}"


def _fmt_usd_received(p) -> str:
    micros = getattr(p, "amount_usd_micros_received", None)
    if micros is None:
        cents = p.amount_usd_cents_received or 0
        return f"${cents / 100:.2f}"
    return _fmt_usd_micros(micros)


def _fmt_token_amount(raw_wei, decimals: int, symbol: str) -> str:
    if not raw_wei:
        return "—"
    try:
        value = Decimal(raw_wei) / Decimal(10 ** decimals)
    except Exception:
        return f"{raw_wei} (raw)"
    s = f"{value:.8f}".rstrip("0").rstrip(".")
    if not s:
        s = "0"
    return f"{s} {symbol}"


def _fmt_rate(rate, source, fetched_at) -> str:
    if rate is None:
        return "—"
    base = f"${rate}/token"
    parts = []
    if source:
        parts.append(source)
    if fetched_at:
        parts.append(fetched_at.strftime("%Y-%m-%d %H:%M UTC"))
    if parts:
        base += f"  ({', '.join(parts)})"
    return base


def landing_page() -> Main:
    return Main(
        Div(
            Div(
                H1("Gateway"),
                P("Crypto payments without friction."),
                P("Funds go straight to your wallet. No custody, no accounts."),
                A(
                    "Sign in",
                    href="/login",
                    cls="btn btn-primary",
                    style="display:inline-flex;width:auto;margin-top:2rem;",
                ),
                cls="landing-hero-inner",
            ),
            cls="landing-hero",
        ),
        Div(
            Div(
                H3("Multi-chain"),
                P("Accept payments on any configured network. One API key works for all."),
                cls="landing-feature",
            ),
            Div(
                H3("Non-custodial"),
                P("Funds go straight to your wallet through a CREATE2 proxy. "
                  "They never pass through the gateway."),
                cls="landing-feature",
            ),
            Div(
                H3("Signed webhooks"),
                P("Every confirmed payment triggers a webhook signed with HMAC-SHA256."),
                cls="landing-feature",
            ),
            cls="landing-features",
        ),
        Div("© Gateway", cls="landing-footer"),
        Title("Gateway · Crypto payments"),
    )


def _badge(payment) -> Div:
    text, color = STATUS_LABELS.get(payment.status,
                                    ("Waiting for your payment...", "amber"))
    return Div(Span(text), cls=f"badge badge-{color}")


def _status_widget(payment) -> Div:
    terminal = payment.status in PaymentStatus.TERMINAL
    attrs = {"id": "payment-status", "cls": "center"}
    ref = payment.public_token or str(payment.id)
    if not terminal:
        attrs.update({
            "hx_get": f"/pay/{ref}/status",
            "hx_trigger": "every 3s",
            "hx_swap": "outerHTML",
        })
    children = [_badge(payment)]
    if not terminal:
        children.append(
            Button(
                "I have paid",
                hx_post=f"/pay/{ref}/sent",
                hx_target="#payment-status",
                hx_swap="outerHTML",
                cls="btn btn-outline mt-2",
            )
        )
    return Div(*children, **attrs)


def _SuccessStatus(payment, token, explorer_url: str | None) -> Div:
    tx = payment.tx_hash or ""
    if tx and not tx.startswith("0x"):
        tx = "0x" + tx
    short_hash = (tx[:10] + "..." + tx[-8:]) if len(tx) > 20 else tx

    children = [
        Div(I(cls="fa-solid fa-check"), cls="success-icon"),
        H3("Payment received!", cls="center",
           style="font-size:1.2rem;font-weight:800;margin-top:0.5rem;"),
        P("Thank you for your payment", cls="center",
          style="color:var(--muted);font-size:0.85rem;margin-top:0.25rem;"),
    ]

    if short_hash:
        tx_children = [
            Span("TX ", style="color:var(--muted);font-size:0.65rem;"),
        ]
        if explorer_url and tx:
            tx_children.append(
                A(
                    short_hash,
                    href=f"{explorer_url.rstrip('/')}/tx/{tx}",
                    target="_blank",
                    rel="noopener",
                    style=(
                        "color:var(--primary);font-family:ui-monospace,monospace;"
                        "font-size:0.75rem;font-weight:600;text-decoration:none;"
                    ),
                    title="Open in block explorer",
                )
            )
        else:
            tx_children.append(
                Span(
                    short_hash,
                    style=(
                        "font-family:ui-monospace,monospace;"
                        "font-size:0.75rem;font-weight:600;"
                    ),
                )
            )
        tx_children.append(
            Button(
                "⧉",
                cls="btn-copy mono",
                **{"data-copy": tx},
                style="margin-left:0.4rem;padding:0.15rem 0.4rem;",
                title="Copy tx hash",
            )
        )
        children.append(Div(*tx_children, cls="tx-pill"))

    if explorer_url and tx:
        children.append(
            A(
                "View on explorer →",
                href=f"{explorer_url.rstrip('/')}/tx/{tx}",
                target="_blank",
                rel="noopener",
                style=(
                    "display:block;margin-top:1rem;color:var(--primary);"
                    "font-size:0.85rem;font-weight:600;text-decoration:none;"
                    "text-align:center;"
                ),
            )
        )

    return Div(*children, id="payment-status", cls="text-center")


def _ErrorStatus(payment) -> Div:
    text, _ = STATUS_LABELS.get(payment.status, ("Payment failed", "red"))
    return Div(
        Div("✗", cls="center", style="font-size:2rem;color:var(--error);"),
        H3(text, cls="center mt-1",
           style="font-size:1.1rem;font-weight:700;"),
        P("Contact the merchant if you believe this is a mistake.",
          cls="center", style="color:var(--muted);font-size:0.85rem;"),
        id="payment-status",
    )


def _WalletCard(symbol: str, chain_name: str, chain_id: int) -> Div:
    return Div(
        Div(Span(chain_name), Span(f"Chain {chain_id}", cls="mono"), cls="row"),
        P("READY TO RECEIVE", cls="label", style="margin-top:1.5rem;"),
        P("Any amount", cls="amount"),
        cls="wallet-card",
    )


def _address_block(proxy_address: str) -> Div:
    return Div(
        P("Or copy the address and send it from your wallet:",
          cls="center",
          style="font-size:0.75rem;color:var(--muted);margin-bottom:0.5rem;"),
        Div(
            Code(proxy_address),
            Button("⧉", cls="btn-copy mono", **{"data-copy": proxy_address},
                   title="Copy address"),
            cls="addr-box",
        ),
    )


def _SetupContent(payment, token, chain_name: str, chain_id: int) -> Div:
    ref = payment.public_token or str(payment.id)
    return Div(
        H2("Crypto payment", cls="pay-title"),
        P("Scan the code to send the payment", cls="pay-sub"),
        _WalletCard(token.symbol, chain_name, chain_id),
        Button(
            "Show payment QR",
            hx_get=f"/pay/{ref}/qr",
            hx_target="#panel-content",
            hx_swap="outerHTML",
            cls="btn btn-primary",
        ),
        Div(_badge(payment), cls="center mt-3"),
        id="panel-content",
    )


def _QRContent(payment, token, chain_name: str, chain_id: int,
               explorer_url: str | None) -> Div:
    uri = eip681_uri(payment.proxy_address, chain_id)
    svg_qr = generate_qr_svg(uri)

    if payment.status in PaymentStatus.TERMINAL_OK:
        status = _SuccessStatus(payment, token, explorer_url)
    elif payment.status in PaymentStatus.TERMINAL_ERR:
        status = _ErrorStatus(payment)
    else:
        status = _status_widget(payment)

    return Div(
        H2("Scan to pay", cls="pay-title"),
        P(f"Send {token.symbol} to the address in the code", cls="pay-sub"),
        Div(NotStr(svg_qr), cls="qr-wrap"),
        _address_block(payment.proxy_address),
        Div(
            Div(Span("Network", cls="key"), Span(chain_name, cls="val"),
                cls="row-between"),
            cls="card", style="padding:1rem;margin-bottom:1rem;",
        ),
        status,
        id="panel-content",
    )


def status_fragment(payment) -> Div:
    if payment.status in PaymentStatus.TERMINAL_OK:
        from app.core.db import get_session
        from app.core.models import Chain, Token
        with get_session() as s:
            token = s.get(Token, payment.token_id)
            chain = s.get(Chain, token.chain_id) if token else None
            explorer_url = getattr(chain, "explorer_url", None) if chain else None
        return _SuccessStatus(payment, token, explorer_url)
    if payment.status in PaymentStatus.TERMINAL_ERR:
        return _ErrorStatus(payment)
    return _status_widget(payment)


def payment_layout(payment, token, chain, merchant: dict) -> Main:
    chain_name = chain.name if chain else "Unknown"
    chain_id = chain.chain_id if chain else 0
    explorer_url = getattr(chain, "explorer_url", None) if chain else None

    if payment.status == PaymentStatus.PENDING:
        content = _SetupContent(payment, token, chain_name, chain_id)
    else:
        content = _QRContent(payment, token, chain_name, chain_id, explorer_url)

    return Main(
        Div(
            Div(
                H1("Gateway"),
                P("On-chain crypto payments"),
                Div(
                    P("· No wallet connection needed"),
                    P("· On-chain verification"),
                    P("· Automatic forwarding"),
                ),
                cls="pay-brand",
            ),
            Div(Div(content, cls="pay-card"), cls="pay-panel"),
            cls="pay-wrap",
        ),
        Title(f"Payment · {merchant['name']}"),
    )


def error_layout(message: str) -> Main:
    return Main(
        Div(
            Div(
                H1("Payment not found"),
                P(message, cls="mt-1"),
                cls="pay-card",
            ),
            cls="pay-panel",
        ),
        Title("Payment not found"),
    )


def login_page(error: str | None = None) -> Main:
    title = "Sign in to the dashboard"
    error_block = None
    if error:
        title = f"⚠ {error[:80]}"
        error_block = P(
            f"ERROR: {error}",
            style=("background:#fee;color:#c00;padding:1rem;"
                   "border-radius:8px;border:1px solid #c00;"
                   "margin-bottom:1rem;font-size:0.85rem;word-break:break-all;"),
        )
    children = []
    if error_block is not None:
        children.append(error_block)
    children.append(H1(title))
    children.append(P("Paste your API key to sign in", cls="sub"))
    children.append(
        Form(
            Label("API Key"),
            Input(type="password", name="api_key", placeholder="sk_live_...",
                  autofocus=True, required=True),
            Button("Sign in", type="submit", cls="btn btn-primary"),
            method="post", action="/login",
        )
    )
    children.append(
        P("Don't have an API key? Contact the gateway operator.",
          cls="center",
          style="font-size:0.75rem;color:var(--muted);margin-top:1.5rem;")
    )
    return Main(
        Div(*children, cls="login-card"),
        Title(title if error else "Sign in · Gateway"),
        cls="login-wrap",
    )


def _dash_layout(merchant: dict, content, active: str = "home") -> Main:
    def _nav(label: str, href: str, key: str):
        cls = "nav-item active" if key == active else "nav-item"
        return A(label, href=href, cls=cls)

    sidebar = Div(
        P("Gateway", cls="logo"),
        _nav("Home", "/dashboard", "home"),
        _nav("Payments", "/dashboard/payments", "payments"),
        _nav("Settings", "/dashboard/settings", "settings"),
        Div(
            P(merchant["name"], cls="name"),
            P(f"id {merchant['id']}", cls="id"),
            A("Sign out", href="/logout", cls="logout-btn"),
            cls="sidebar-footer",
        ),
        cls="sidebar",
    )
    return Main(
        sidebar,
        Div(content, cls="main"),
        Title("Dashboard · Gateway"),
        cls="dash",
    )


def _stat_card(label: str, value, color: str = "") -> Div:
    cls = f"value {color}".strip()
    return Div(P(label, cls="label"), P(str(value), cls=cls), cls="stat")


def _curl_example() -> Div:
    example = (
        'curl -X POST https://your-gateway.com/api/payments \\\n'
        '  -H "X-API-Key: sk_live_..." \\\n'
        '  -H "Content-Type: application/json" \\\n'
        '  -d \'{\n'
        '    "external_ref": "order-123",\n'
        '    "amount_usd_cents": 1000,\n'
        '    "token_id": 1,\n'
        '    "wallet_address": "0xYourWallet...",\n'
        '    "webhook_url": "https://your-site.com/webhook"\n'
        '  }\''
    )
    return Div(example, cls="code-block")


def dashboard_home(merchant: dict, stats: dict) -> Main:
    micros = stats.get("usd_received_micros")
    if micros is None:
        usd_display = f"${stats.get('usd_received', 0):.2f}"
    else:
        usd_display = _fmt_usd_micros(int(micros))

    content = Div(
        H1(f"Hello, {merchant['name']}"),
        P("Overview of your payments", cls="sub"),
        Div(
            _stat_card("Total payments", stats["total"]),
            _stat_card("Confirmed", stats["confirmed"], "ok"),
            _stat_card("Pending", stats["pending"], "warn"),
            _stat_card("USD received", usd_display, "primary"),
            cls="stats",
        ),
        Div(
            H3("Create a payment"),
            P("Call the API with your key in the X-API-Key header.",
              style="color:var(--muted);font-size:0.9rem;margin-bottom:1rem;"),
            _curl_example(),
            cls="card",
        ),
    )
    return _dash_layout(merchant, content, active="home")


def _badge_row(status: str) -> Span:
    _, color = STATUS_LABELS.get(status, (status, "amber"))
    return Span(status, cls=f"badge badge-{color}", style="font-size:0.75rem;")


def dashboard_payments(merchant: dict, payments: list) -> Main:
    if not payments:
        table = Div(
            Div("▤", cls="big"),
            P("No payments yet."),
            P("They will appear here as soon as you call the API.",
              style="font-size:0.85rem;margin-top:0.25rem;"),
            cls="card empty",
        )
    else:
        rows = []
        for p in payments:
            rows.append(
                Div(
                    Div(
                        Div(p.external_ref,
                            style="font-weight:600;font-size:0.9rem;"),
                        Div(p.created_at.strftime("%Y-%m-%d %H:%M"),
                            style="color:var(--muted);font-size:0.75rem;"),
                    ),
                    Div(_badge_row(p.status)),
                    Div(
                        Div(_fmt_usd_received(p),
                            style="font-weight:700;text-align:right;"),
                        Div(f"id {p.id}",
                            style="color:var(--muted);font-size:0.75rem;text-align:right;"),
                    ),
                    A("→", href=f"/dashboard/payments/{p.id}",
                      style="font-size:1.2rem;"),
                    cls="row-between",
                    style="align-items:center;",
                )
            )
        table = Div(*rows, cls="card")

    content = Div(
        H1("Payments"),
        P(f"{len(payments)} payment(s) in total", cls="sub"),
        table,
    )
    return _dash_layout(merchant, content, active="payments")


def dashboard_payment_detail(merchant: dict, payment, token, chain) -> Main:
    def row(k: str, v):
        return Div(Span(k, cls="key"), Span(str(v), cls="val mono"),
                   cls="row-between")

    def _tx_row(label: str, tx_hash: str | None):
        if not tx_hash:
            return row(label, "—")
        tx = tx_hash if tx_hash.startswith("0x") else "0x" + tx_hash
        explorer = getattr(chain, "explorer_url", None) if chain else None
        if explorer:
            tx_el = A(
                tx,
                href=f"{explorer.rstrip('/')}/tx/{tx}",
                target="_blank",
                rel="noopener",
                cls="mono",
                style=(
                    "color:var(--primary);word-break:break-all;"
                    "text-align:right;"
                ),
            )
        else:
            tx_el = Span(tx, cls="mono", style="word-break:break-all;")
        return Div(
            Span(label, cls="key"),
            Div(
                tx_el,
                Button(
                    "⧉",
                    cls="btn-copy mono",
                    **{"data-copy": tx},
                    title="Copy transaction hash",
                ),
                style=(
                    "display:flex;gap:0.5rem;align-items:center;"
                    "justify-content:flex-end;max-width:70%;"
                ),
            ),
            cls="row-between",
        )

    expected_micros = (payment.amount_usd_cents_expected or 0) * 10_000
    received_micros = payment.amount_usd_micros_received or 0
    ratio_note = None
    if expected_micros > 0:
        ratio = received_micros / expected_micros
        if ratio < 0.99:
            ratio_note = f"{ratio * 100:.2f}% of expected"
        elif ratio > 1.01:
            ratio_note = f"{ratio * 100:.2f}% of expected (overpaid)"

    token_symbol = token.symbol if token else "?"
    token_decimals = token.decimals if token else 18
    ref = payment.public_token or str(payment.id)

    content = Div(
        A("← Back to payments", href="/dashboard/payments",
          style="font-size:0.85rem;font-weight:600;"),
        H1(payment.external_ref, style="margin-top:1rem;"),
        Div(_badge_row(payment.status), cls="mt-1"),

        Div(
            H3("Amounts"),
            row("Token amount", _fmt_token_amount(
                payment.amount_token_received, token_decimals, token_symbol,
            )),
            row("Token price", _fmt_rate(
                payment.rate_used, payment.rate_source, payment.rate_fetched_at,
            )),
            row("USD received", _fmt_usd_received(payment)),
            row("USD expected",
                f"${(payment.amount_usd_cents_expected or 0) / 100:.2f}"),
            *([row("Ratio", ratio_note)] if ratio_note else []),
            cls="card mt-3",
        ),

        Div(
            H3("Details"),
            row("Payment ID", payment.id),
            row("Public token", payment.public_token or "—"),
            row("Token", token.symbol if token else "—"),
            row("Network", chain.name if chain else "—"),
            row("Destination wallet", payment.wallet_address),
            row("Proxy", payment.proxy_address),
            row("Webhook URL", payment.webhook_url),
            _tx_row("Deploy tx", payment.deploy_tx_hash),
            _tx_row("Payment tx", payment.tx_hash),
            row("Block", payment.block_number or "—"),
            row("Created", payment.created_at.strftime("%Y-%m-%d %H:%M:%S")),
            row("Detected",
                payment.detected_at.strftime("%Y-%m-%d %H:%M:%S")
                if payment.detected_at else "—"),
            row("Confirmed",
                payment.confirmed_at.strftime("%Y-%m-%d %H:%M:%S")
                if payment.confirmed_at else "—"),
            cls="card mt-3",
        ),

        Div(
            H3("Payment link"),
            P("Share this link with your customer:",
              style="color:var(--muted);font-size:0.85rem;margin-bottom:0.75rem;"),
            Div(f"/pay/{ref}", cls="code-block"),
            cls="card",
        ),
    )
    return _dash_layout(merchant, content, active="payments")


def dashboard_settings(merchant: dict) -> Main:
    content = Div(
        H1("Settings"),
        P("Your account information", cls="sub"),
        Div(
            H3("Merchant"),
            Div(Span("Name", cls="key"),
                Span(merchant["name"], cls="val"), cls="row-between"),
            Div(Span("Gateway ID", cls="key"),
                Span(str(merchant["id"]), cls="val mono"), cls="row-between"),
            Div(Span("Fee", cls="key"),
                Span(f"{merchant['fee_bps_default'] / 100:.2f}%"
                     if merchant["fee_bps_default"] is not None
                     else "no fee", cls="val"),
                cls="row-between"),
            cls="card",
        ),
        Div(
            H3("API Key"),
            P("For security, we only store the hash of your API key. "
              "If you lost it, contact the gateway operator to issue a new one.",
              style="color:var(--muted);font-size:0.9rem;"),
            cls="card",
        ),
    )
    return _dash_layout(merchant, content, active="settings")
