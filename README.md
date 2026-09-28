# Gateway_Onchain: Non-Custodial Payment Gateway with CREATE2 (Vyper)

Multi-chain crypto payment gateway. Charge in cryptocurrencies without custody:
funds are forwarded directly to the merchant's wallet through a CREATE2 proxy
deployed on-chain.

Designed to take advantage of high-performance networks like
**Monad**, where deploying one proxy per payment is economically viable.

[![Vyper](https://img.shields.io/badge/Vyper-0.4.x-blue)](https://vyperlang.org/)
[![Python](https://img.shields.io/badge/Python-3.11+-green)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

---

## Table of contents

- [Description](#description)
- [Architecture](#architecture)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Contract deployment](#contract-deployment)
- [Running](#running)
- [API](#api)
- [Webhooks](#webhooks)
- [Project structure](#project-structure)
- [Development](#development)
- [Security](#security)
- [License](#license)

---

## Description

**Gateway** is a service that lets any merchant accept cryptocurrency payments
without having to custody funds, manage wallets, or integrate directly with the
blockchain.

The merchant calls an API, the user pays to a unique address generated on the
spot, and the money arrives directly to the merchant's wallet. The gateway only
acts as an orchestrator: **it never touches the funds**.

### How it works

1. The merchant requests a payment via `POST /api/payments`, specifying the
   destination wallet, webhook URL, and amount in USD.
2. The gateway calculates a unique proxy address using CREATE2 and returns it
   to the merchant.
3. The user pays to that address from their wallet.
4. The worker detects the payment, deploys the proxy on-chain, and the proxy
   forwards the entire balance to the merchant's wallet.
5. The gateway sends an HMAC-SHA256-signed webhook to the merchant to notify
   that the payment has been confirmed.

---

## Architecture

```
       ┌──────────────────┐
       │     Merchant     │
       └────────┬─────────┘
                │ POST /api/payments
                ▼
       ┌──────────────────┐
       │     Gateway      │◄────── API (FastHTML)
       │   (app + worker) │
       └────────┬─────────┘
                │ creates order, exposes proxy address
                ▼
       ┌──────────────────┐
       │      User        │────── sends crypto
       └────────┬─────────┘
                │
                ▼
       ┌──────────────────┐
       │  CREATE2 Proxy   │────── forwards 100% to merchant
       └────────┬─────────┘
                │
                ▼
       ┌──────────────────┐
       │ Merchant Wallet  │
       └──────────────────┘

       Gateway worker:
         · polling  → detects balance in the proxy
         · deploy   → deploys the proxy on-chain
         · webhooks → notifies the merchant
         · rates    → updates USD rates
```

### Internal services

| Service | Description | Port |
|----------|-------------|--------|
| **app** | HTTP API, dashboard, and public UI | `8000` |
| **worker** | Background processes (4 threads) | — |
| **DB** | SQLite (default) or PostgreSQL | — |

---

## Features

- **Non-custodial**: money goes directly from the user to the merchant. The
  gateway never has access to the funds.
- **Unique address per payment**: CREATE2 guarantees that each payment has its
  own proxy, with no collisions or memos.
- **Multi-chain**: add as many networks as you want from the `chains` table.
- **Multi-token**: native and ERC-20 per chain.
- **Signed webhooks**: HMAC-SHA256 with exponential retries.
- **Merchant dashboard**: login by API key, payment history, transaction
  details.
- **Public payment UI**: page with QR (SVG generated server-side) and copyable
  address. No JavaScript for the QR.
- **Idempotency**: processed events recorded by `(tx_hash, log_index)`.
- **Payment expiration**: 30 minutes by default. The worker keeps watching for
  24 h in case it arrives late.
- **Exchange rates**: CoinGecko integration, history stored in DB.
- **Rotated logs**: 5 MB files with 5 backups.

---

## Requirements

- **Python** 3.11+
- **Vyper** 0.4.x (`pip install vyper`)
- A **wallet** with funds to pay for gas
- An **RPC URL** (Infura, Alchemy, public, etc.)
- **Contracts deployed** on the target network (see
  [Contract deployment](#contract-deployment))

---

## Installation

```bash
git clone https://github.com/<your-user>/gateway.git
cd gateway

python -m venv .venv
source .venv/bin/activate    # Linux/macOS
# .venv\Scripts\activate     # Windows

pip install -r requirements.txt
```

For development:

```bash
pip install -r requirements-dev.txt
```

---

## Configuration

Copy the template and edit it:

```bash
cp .env.example .env
```

### Environment variables

| Variable | Description | Example |
|----------|-------------|---------|
| `RPC_URL` | RPC node URL | `https://testnet-rpc.monad.xyz` |
| `CHAIN_ID` | Chain ID | `10143` |
| `CHAIN_NAME` | Human-readable chain name | `monad-testnet` |
| `CHAIN_EXPLORER` | Explorer URL | `https://testnet.monadvision.com` |
| `FACTORY_ADDRESS` | Deployed Factory address | `0x...` |
| `MASTER_ADDRESS` | Deployed Master address | `0x...` |
| `PRIVATE_KEY` | Private key of the gateway wallet | `0x...` |
| `SESSION_SECRET` | Secret for signing session cookies | long random string |
| `DATABASE_URL` | Database URL | `sqlite:///./gateway.db` |
| `COINGECKO_API_KEY` | CoinGecko API key (optional) | |
| `LOG_LEVEL` | Log level | `INFO` |

Generate a `SESSION_SECRET` with:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## Contract deployment

Contracts are deployed **once per network**. The script compiles them, uploads
them to the blockchain, and updates `.env` with the resulting addresses.

```bash
python scripts/deploy_contracts.py
```

Expected output:

```
[INFO] Connecting to https://testnet-rpc.monad.xyz
[INFO] Wallet:  0xYourWallet...
[INFO] Balance: 0.5 MON
[INFO] Compiling contracts...
[INFO] Deploying Master (Forwarded)...
[INFO]   MASTER_ADDRESS = 0x1234...
[INFO] Deploying Factory...
[INFO]   FACTORY_ADDRESS = 0x5678...
[INFO] .env updated
```

After deployment, initialize the DB:

```bash
python scripts/init_db.py
```

And create the first API key for your merchant:

```bash
python scripts/create_api_key.py --name "My Store"
```

**Save the key** it prints. It is shown only once.

---

## Running

The gateway consists of **two independent processes**. Start both.

### Terminal 1 — worker

```bash
python -m app.worker.main
```

It shows the 4 started cycles:

```
[INFO] gateway_worker | [polling]   started (every 60s)
[INFO] gateway_worker | [deploy]    started (every 15s)
[INFO] gateway_worker | [webhooks]  started (every 30s)
[INFO] gateway_worker | [rates]     started (every 300s)
```

### Terminal 2 — app

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` in your browser.

### Production

For production, use `systemd` or `supervisord` to keep both processes alive.
Example with systemd:

```ini
# /etc/systemd/system/gateway-app.service
[Unit]
Description=Gateway App
After=network.target

[Service]
User=gateway
WorkingDirectory=/opt/gateway
ExecStart=/opt/gateway/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

Analogous for `gateway-worker.service` with
`ExecStart=.../python -m app.worker.main`.

---

## API

### Authentication

All API calls require the `X-API-Key` header:

```
X-API-Key: sk_live_abc123...
```

### Create a payment

```http
POST /api/payments
Content-Type: application/json
X-API-Key: sk_live_...

{
  "external_ref": "order-123",
  "amount_usd_cents": 1000,
  "token_id": 1,
  "wallet_address": "0xYourMerchantWallet",
  "webhook_url": "https://your-site.com/webhook",
  "webhook_secret": "optional",
  "merchant_name": "My Store"
}
```

**Response** (`201 Created`):

```json
{
  "payment_id": 1,
  "external_ref": "order-123",
  "status": "pending",
  "proxy_address": "0xABC...",
  "amount_usd_cents_expected": 1000,
  "amount_token_suggested": "3813882532418001",
  "expires_at": "2026-09-27T18:00:00+00:00"
}
```

The merchant should redirect the user to
`https://gateway/pay/{payment_id}`.

### Get a payment

```http
GET /api/payments/{payment_id}
X-API-Key: sk_live_...
```

Returns the current payment status.

### Error codes

| Code | Meaning |
|--------|-------------|
| `400` | Invalid data (missing fields, malformed wallet, etc.) |
| `401` | Invalid or missing API key |
| `404` | Payment not found or does not belong to this merchant |
| `500` | Internal error |

---

## Webhooks

When a payment is confirmed, the gateway sends a `POST` to the `webhook_url`
that the merchant provided when creating the payment.

### Payload

```json
{
  "payment_id": 1,
  "external_ref": "order-123",
  "merchant_name": "My Store",
  "status": "confirmed",
  "token_symbol": "MON",
  "amount_token_received": "2000000000000000",
  "amount_usd_cents_received": 519,
  "rate_used": "2600.5",
  "rate_source": "coingecko",
  "wallet_address": "0xMerchant...",
  "proxy_address": "0xProxy...",
  "tx_hash": "0xabc123...",
  "block_number": 12345,
  "explorer_url": "https://testnet.monadvision.com/tx/0xabc123...",
  "confirmed_at": "2026-09-27T18:02:11+00:00",
  "fee_bps_used": null,
  "fee_amount_token": null
}
```

### Headers

```
X-Webhook-Signature: <hex>
X-Webhook-Timestamp: <unix_ts>
X-Webhook-Payment-Id: <id>
Content-Type: application/json
```

### Verify the signature

The body is signed with HMAC-SHA256 over `timestamp + "." + body`.

Example in Python:

```python
import hashlib
import hmac

def verify_signature(secret: str, timestamp: str,
                     body: bytes, signature: str) -> bool:
    message = f"{timestamp}.".encode() + body
    expected = hmac.new(
        secret.encode(), message, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
```

The merchant should:
1. Read the `X-Webhook-Timestamp` header and the `X-Webhook-Signature` header.
2. Recompute the signature with its copy of `webhook_secret`.
3. Compare with `hmac.compare_digest` (constant-time comparison).
4. Reject if the timestamp is older than 5 minutes (prevents replay attacks).

### Retries

If the merchant does not respond with `2xx`, the gateway retries with
exponential backoff:

| Attempt | Wait |
|---------|--------|
| 1 | immediate |
| 2 | 30 s |
| 3 | 1 min |
| 4 | 5 min |
| 5 | 15 min |
| 6 | 1 h |
| 7 | 6 h |

After 7 failed attempts, a critical error is logged and retries stop.

**The merchant must be idempotent**: the same webhook may arrive more than
once due to retries or duplicate on-chain events.

---

## Project structure

```
Gateway/
├── app/
│   ├── main.py           # FastHTML entry point
│   ├── routes.py         # HTTP routes
│   ├── templates.py      # visual components
│   ├── config.py         # .env loading
│   ├── core/
│   │   ├── db.py         # SQLAlchemy
│   │   ├── models.py     # tables
│   │   ├── blockchain.py # contract interaction
│   │   ├── rates.py      # USD rates
│   │   ├── payments.py   # business logic
│   │   ├── webhooks.py   # signing and sending
│   │   └── auth.py       # sessions
│   ├── worker/
│   │   ├── main.py       # startup
│   │   ├── polling.py    # balance detection
│   │   ├── deploy.py     # proxy deployment
│   │   ├── webhooks.py   # retries
│   │   └── rates.py      # rate updates
│   └── abis/             # contract ABIs
├── scripts/
│   ├── init_db.py
│   ├── create_api_key.py
│   ├── deploy_contracts.py
│   └── inspect_db.py
├── logs/
├── .env.example
├── requirements.txt
└── README.md
```

---

## Development

### Inspect the database

```bash
python scripts/inspect_db.py                  # general summary
python scripts/inspect_db.py --pagos          # last 20 payments
python scripts/inspect_db.py --comercio 1     # merchant 1 details
python scripts/inspect_db.py --api-keys       # issued API keys
python scripts/inspect_db.py --chains         # chains and tokens
python scripts/inspect_db.py --webhooks       # webhook attempts
```

### Add a merchant

```bash
python scripts/create_api_key.py --name "New Store"
```

### Deactivate an API key

```bash
python scripts/create_api_key.py --deactivate 3
```

### Full test cycle

```bash
python run_full_cycle.py
```

Starts an in-memory tester, simulates a payment from start to finish, and
verifies that the webhook arrives with a valid signature. Useful for validating
changes without spending real gas.

---

## Security

- **The API key is shown only once.** If lost, you must issue another one and
  deactivate the old one.
- **We only store the SHA-256 hash** of the API key. Neither the plaintext
  value nor a reversible hash.
- **The `PRIVATE_KEY` must be a dedicated wallet**, not your main wallet. It
  only pays gas; it never custodies user funds.
- **Always verify the HMAC signature** of webhooks before processing them.
- **Use HTTPS in production.** Webhooks travel over the network; without TLS,
  anyone can intercept them.
- **Do not expose the dashboard without authentication.** In production, use a
  reverse proxy with basic authentication or a VPN.
- **Rate limiting recommended** on `POST /api/payments` to prevent abuse.

### Vulnerability reporting

If you find a security vulnerability, send an email to
`security@yourdomain.com` instead of opening a public issue.

---

## License

MIT © 2026 <guilleselon>

> ⚡ **Key point**: The proxy is only deployed if there is an actual payment, saving gas on orders that are never executed.

---

## ✨ Key features

- **Non‑custodial**: Funds are never held; they flow directly to the `processor` as soon as the proxy is initialised or receives funds.
- **Deploy‑on‑demand**: The proxy is created only when there are funds to forward.
- **Deterministic addresses**: The address can be pre‑calculated without deployment.
- **Clear events**: `paid` includes `order_id` and `amount`; the backend can sum multiple payments for the same order.
- **Smart gas handling**: `msg.gas - 10000` avoids failures due to fixed limits.
- **Reentrancy protection**: Uses `_locked`.

---

## ⛔ Important limitations

| Limitation | Detail |
|------------|---------|
| **Native ETH only** | Does not support ERC‑20 tokens. |
| **Dynamic gas** | If the `processor` consumes a lot of gas, the 10,000 margin may be insufficient. |
| **No expiration** | Once deployed, the proxy accepts payments indefinitely. |
| **No fees** | The entire amount goes to the `processor`; there is no fee mechanism. |
| **No automatic refunds** | Cannot issue on‑chain refunds from the proxy. |
| **No KYC/AML** | Anonymous; compliance must be handled off‑chain. |

---

## 🔒 Security

- The `processor` is immutable after initialisation.
- The proxy uses `_locked` to prevent reentrancy.
- `initialize()` can only be called once.
- **Recommendation**: Use an **EOA** as the `processor` to avoid excessive gas risks.

---

## 🛠️ Technologies

- **Vyper** `^0.4.3`
- **EIP‑1167** (Minimal Proxy)
- **CREATE2** for deterministic addresses

---

## 📄 License

MIT
