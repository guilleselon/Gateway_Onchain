"""
Gateway FastHTML app.

Serves:
- Public landing page.
- Public payment UI.
- Merchant login and dashboard.
- Internal API for merchants (X-API-Key).

No CDN. Minimal inline CSS.
"""

import atexit
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fasthtml.common import Link, Script, Style, Title, fast_app
from starlette.middleware.sessions import SessionMiddleware

from app.config import RPC_URL, SESSION_SECRET, setup_logging
from app.core.db import engine, ensure_db_initialized
from app.routes import register

# The web service requires SESSION_SECRET to sign session cookies.
# The worker does not import this module, so it does not need it.
if not SESSION_SECRET:
    raise RuntimeError(
        "SESSION_SECRET is not set. "
        "The web service requires it to sign session cookies."
    )

# Ensure the DB connection pool is closed cleanly on shutdown (SIGTERM/SIGINT).
# Important on platforms that restart services frequently (Render, Railway)
# to avoid leaving orphan connections in PostgreSQL.
atexit.register(engine.dispose)

log = setup_logging("gateway_app")

# Auto-initialize the database on first boot.
# Idempotent: creates tables, seeds chain/tokens, and prints a demo API key
# if there is none. Safe to run every time the app starts.
try:
    ensure_db_initialized()
except Exception as e:
    log.exception(f"DB auto-initialization failed: {e}")

_rpc_for_js = RPC_URL if (RPC_URL and RPC_URL.startswith("http")) else ""

_CSS = """
:root {
  --bg: #f8f9fb;
  --card: #ffffff;
  --border: #e5e7eb;
  --text: #111827;
  --muted: #6b7280;
  --primary: #6d28d9;
  --primary-hover: #5b21b6;
  --ok: #059669;
  --ok-bg: #ecfdf5;
  --warn: #d97706;
  --warn-bg: #fffbeb;
  --error: #dc2626;
  --error-bg: #fef2f2;
  --info: #2563eb;
  --info-bg: #eff6ff;
}

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  font-size: 15px;
  line-height: 1.5;
  min-height: 100vh;
}

a { color: var(--primary); text-decoration: none; }
a:hover { text-decoration: underline; }

code, .mono { font-family: ui-monospace, "SF Mono", Consolas, monospace; font-size: 0.9em; }

/* --- Landing --- */
.landing-hero {
  background: linear-gradient(135deg, #1e1b4b 0%, #4338ca 100%);
  color: #fff;
  padding: 6rem 2rem;
  display: flex;
  justify-content: center;
  text-align: center;
}
.landing-hero-inner { max-width: 620px; }
.landing-hero h1 { font-size: 3rem; font-weight: 800; margin-bottom: 1rem; letter-spacing: -0.02em; }
.landing-hero p { font-size: 1.1rem; opacity: 0.8; margin-bottom: 0.5rem; }

.landing-features {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 1.5rem;
  max-width: 1000px;
  margin: 4rem auto;
  padding: 0 2rem;
}
.landing-feature {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1.5rem;
}
.landing-feature h3 { font-size: 1.1rem; font-weight: 700; margin-bottom: 0.5rem; }
.landing-feature p { color: var(--muted); font-size: 0.9rem; line-height: 1.5; }

.landing-footer {
  text-align: center;
  padding: 2rem;
  color: var(--muted);
  font-size: 0.85rem;
  border-top: 1px solid var(--border);
}

/* --- Public payment layout --- */
.pay-wrap { display: grid; grid-template-columns: 1fr 1fr; min-height: 100vh; }
.pay-brand {
  background: linear-gradient(135deg, #1e1b4b 0%, #4338ca 100%);
  color: #fff;
  padding: 3rem;
  display: flex;
  flex-direction: column;
  justify-content: center;
}
.pay-brand h1 { font-size: 2rem; font-weight: 800; margin-bottom: 0.5rem; }
.pay-brand p { opacity: 0.75; margin-bottom: 1rem; }
.pay-brand ul { list-style: none; }
.pay-brand li { padding: 0.4rem 0; opacity: 0.85; font-size: 0.9rem; }

.pay-panel {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 2rem;
}
.pay-card {
  background: var(--card);
  border-radius: 16px;
  padding: 2rem;
  box-shadow: 0 10px 40px rgba(0,0,0,0.08);
  max-width: 440px;
  width: 100%;
}

.pay-title { font-size: 1.4rem; font-weight: 700; text-align: center; margin-bottom: 0.3rem; }
.pay-sub { color: var(--muted); text-align: center; font-size: 0.9rem; margin-bottom: 1.5rem; }

.wallet-card {
  background: linear-gradient(135deg, #6d28d9 0%, #a21caf 100%);
  color: #fff;
  padding: 1.5rem;
  border-radius: 14px;
  margin-bottom: 1.5rem;
}
.wallet-card .row { display: flex; justify-content: space-between; font-size: 0.8rem; opacity: 0.85; }
.wallet-card .amount { font-size: 1.8rem; font-weight: 800; margin-top: 1.5rem; }
.wallet-card .label { font-size: 0.7rem; letter-spacing: 0.1em; opacity: 0.7; margin-bottom: 0.3rem; }

.qr-wrap {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1.25rem;
  display: flex;
  justify-content: center;
  margin-bottom: 1rem;
}
.qr-wrap svg { width: 220px; height: 220px; }

.addr-box {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.6rem 0.8rem;
  display: flex;
  align-items: flex-start;
  gap: 0.5rem;
  margin-bottom: 1rem;
}
.addr-box code { word-break: break-all; flex: 1; font-size: 0.75rem; color: var(--muted); }

.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  padding: 0.75rem 1.25rem;
  border-radius: 10px;
  border: none;
  cursor: pointer;
  font-family: inherit;
  font-size: 0.95rem;
  font-weight: 600;
  text-decoration: none;
  transition: all 0.15s;
}
.btn:hover { text-decoration: none; }
.btn-primary { background: var(--primary); color: #fff; width: 100%; }
.btn-primary:hover { background: var(--primary-hover); }
.btn-outline { background: transparent; border: 2px solid var(--primary); color: var(--primary); width: 100%; }
.btn-outline:hover { background: var(--primary); color: #fff; }
.btn-copy { background: var(--bg); border: 1px solid var(--border); padding: 0.4rem 0.6rem; border-radius: 6px; cursor: pointer; }
.btn-copy:hover { background: var(--border); }

.badge {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  padding: 0.4rem 0.8rem;
  border-radius: 20px;
  font-size: 0.85rem;
  font-weight: 600;
  border: 1px solid;
}
.badge-amber   { background: var(--warn-bg);    color: var(--warn);    border-color: var(--warn); }
.badge-blue    { background: var(--info-bg);    color: var(--info);    border-color: var(--info); }
.badge-emerald { background: var(--ok-bg);      color: var(--ok);      border-color: var(--ok); }
.badge-red     { background: var(--error-bg);   color: var(--error);   border-color: var(--error); }

.center { text-align: center; }
.mt-1 { margin-top: 0.5rem; }
.mt-2 { margin-top: 1rem; }
.mt-3 { margin-top: 1.5rem; }

/* --- Login --- */
.login-wrap { min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 2rem; }
.login-card { background: var(--card); border-radius: 16px; padding: 2.5rem; box-shadow: 0 10px 40px rgba(0,0,0,0.08); max-width: 400px; width: 100%; }
.login-card h1 { font-size: 1.3rem; text-align: center; margin-bottom: 0.3rem; }
.login-card .sub { color: var(--muted); text-align: center; font-size: 0.85rem; margin-bottom: 1.5rem; }

/* --- Forms --- */
label { display: block; font-size: 0.8rem; font-weight: 600; margin-bottom: 0.4rem; }
input[type="text"], input[type="password"] {
  width: 100%;
  padding: 0.7rem 0.9rem;
  border: 2px solid var(--border);
  border-radius: 8px;
  font-family: inherit;
  font-size: 0.95rem;
  margin-bottom: 1rem;
  outline: none;
  transition: border-color 0.15s;
}
input:focus { border-color: var(--primary); }

.alert {
  padding: 0.7rem 1rem;
  border-radius: 8px;
  font-size: 0.85rem;
  margin-top: 1rem;
  text-align: center;
}
.alert-error { background: var(--error-bg); color: var(--error); border: 1px solid var(--error); }

/* --- Dashboard --- */
.dash { display: grid; grid-template-columns: 220px 1fr; min-height: 100vh; }
.sidebar { background: var(--card); border-right: 1px solid var(--border); padding: 1.5rem 1rem; display: flex; flex-direction: column; }
.sidebar .logo { font-weight: 800; font-size: 1.1rem; padding: 0 0.5rem; margin-bottom: 2rem; color: var(--primary); }
.nav-item {
  display: block;
  padding: 0.6rem 0.9rem;
  border-radius: 8px;
  color: var(--muted);
  font-size: 0.9rem;
  margin-bottom: 0.25rem;
  text-decoration: none;
  transition: all 0.1s;
}
.nav-item:hover { background: var(--bg); color: var(--text); text-decoration: none; }
.nav-item.active { background: var(--primary); color: #fff; }

.sidebar-footer { margin-top: auto; padding: 0.75rem 0.5rem; border-top: 1px solid var(--border); font-size: 0.8rem; }
.sidebar-footer .name { font-weight: 600; }
.sidebar-footer .id { color: var(--muted); font-size: 0.75rem; }
.logout-btn { background: none; border: none; color: var(--muted); cursor: pointer; font-family: inherit; font-size: 0.8rem; padding: 0.4rem 0; }
.logout-btn:hover { color: var(--error); text-decoration: none; }

.main { padding: 2rem; overflow-y: auto; }
.main h1 { font-size: 1.8rem; font-weight: 800; margin-bottom: 0.3rem; }
.main h2 { font-size: 1.15rem; font-weight: 700; margin-bottom: 1rem; }
.main .sub { color: var(--muted); margin-bottom: 2rem; font-size: 0.95rem; }

.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 1rem; margin-bottom: 2rem; }
.stat { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 1.25rem; }
.stat .label { color: var(--muted); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; }
.stat .value { font-size: 1.6rem; font-weight: 800; margin-top: 0.3rem; }
.stat .value.ok { color: var(--ok); }
.stat .value.warn { color: var(--warn); }
.stat .value.primary { color: var(--primary); }

.card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 1.5rem; margin-bottom: 1.5rem; }
.card h3 { font-size: 1rem; font-weight: 700; margin-bottom: 1rem; }

.row-between { display: flex; justify-content: space-between; padding: 0.5rem 0; border-bottom: 1px solid var(--border); font-size: 0.9rem; }
.row-between:last-child { border-bottom: none; }
.row-between .key { color: var(--muted); }
.row-between .val { font-weight: 500; text-align: right; word-break: break-all; max-width: 60%; }

.code-block { background: #1e1b4b; color: #e0e7ff; border-radius: 10px; padding: 1rem; font-family: ui-monospace, monospace; font-size: 0.8rem; overflow-x: auto; white-space: pre; }

.empty { text-align: center; padding: 3rem 1rem; color: var(--muted); }
.empty .big { font-size: 2.5rem; opacity: 0.3; margin-bottom: 0.5rem; }

@media (max-width: 768px) {
  .pay-wrap { grid-template-columns: 1fr; }
  .pay-brand { display: none; }
  .dash { grid-template-columns: 1fr; }
  .sidebar { flex-direction: row; overflow-x: auto; padding: 0.75rem; }
  .sidebar .logo, .sidebar-footer { display: none; }
  .nav-item { white-space: nowrap; margin-bottom: 0; margin-right: 0.25rem; }
  .landing-hero h1 { font-size: 2rem; }
}
"""

_JS = """
(function(){
    document.addEventListener('click', function (e) {
        var btn = e.target.closest('.btn-copy');
        if (!btn) return;
        var addr = btn.dataset.copy;
        if (!addr || !navigator.clipboard) return;
        navigator.clipboard.writeText(addr).then(function () {
            var old = btn.textContent;
            btn.textContent = 'OK';
            setTimeout(function () { btn.textContent = old; }, 1500);
        });
    });

    async function updateBlock() {
        var rpc = window.__GATEWAY_RPC;
        if (!rpc) return;
        try {
            var res = await fetch(rpc, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    jsonrpc: '2.0', id: 1,
                    method: 'eth_blockNumber', params: []
                })
            });
            var data = await res.json();
            if (data.result) {
                var n = parseInt(data.result, 16);
                var el = document.getElementById('live-block');
                if (el) el.textContent = n.toLocaleString();
            }
        } catch (e) { }
    }
    updateBlock();
    setInterval(updateBlock, 8000);
})();
"""

app, rt = fast_app(
    pico=False,
    Title("Onchain Gateway"),
    hdrs=(
        Link(
            rel="icon",
            href=(
                "data:image/svg+xml,"
                "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>"
                "<circle cx='50' cy='50' r='50' fill='%236d28d9'/>"
                "<path d='M55 20 L35 55 L48 55 L45 80 L65 45 L52 45 Z' fill='white'/>"
                "</svg>"
            ),
        ),

        Style(_CSS),
        Script(f"window.__GATEWAY_RPC = {_rpc_for_js!r};"),
        Script(_JS),
    ),
)

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="gw_session",
    max_age=14 * 24 * 3600,
    same_site="lax",
    https_only=False,
)

register(app, rt)


if __name__ == "__main__":
    import uvicorn
    log.info("Starting gateway at http://0.0.0.0:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False, log_level="info")
