"""
Gateway configuration.

Loads environment variables, exposes logging utilities, ABIs, and
functions to instantiate Web3 and contract objects on demand.

Active chains live in the database (table 'chains'). The chain variables
in .env are only used for the initial bootstrap (scripts/init_db.py).
"""

import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv
from web3 import Web3

# --- Base paths ---
BASE_DIR = Path(__file__).resolve().parent.parent
LOGS_DIR = BASE_DIR / "logs"
ABIS_DIR = Path(__file__).resolve().parent / "abis"

# --- Load .env from the repository root ---
load_dotenv(BASE_DIR / ".env")

# --- Gateway global variables ---
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'gateway.db'}")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# --- Dashboard sessions ---
SESSION_SECRET = os.getenv("SESSION_SECRET", "")

# --- Quotas and limits ---
DAILY_DEPLOY_QUOTA = int(os.getenv("DAILY_DEPLOY_QUOTA", "100"))
MIN_GATEWAY_BALANCE_WEI = int(os.getenv(
    "MIN_GATEWAY_BALANCE_WEI",
    str(int(0.05 * 10**18)),  # 0.05 native token by default
))

# --- Initial chain bootstrap variables ---
CHAIN_NAME = os.getenv("CHAIN_NAME", "sepolia")
CHAIN_ID_RAW = os.getenv("CHAIN_ID", "0")
RPC_URL = os.getenv("RPC_URL")
FACTORY_ADDRESS_RAW = os.getenv("FACTORY_ADDRESS")
MASTER_ADDRESS_RAW = os.getenv("MASTER_ADDRESS")
CHAIN_EXPLORER = os.getenv("CHAIN_EXPLORER", "")
CHAIN_CONFIRMATIONS = int(os.getenv("CHAIN_CONFIRMATIONS", "5"))

# --- Early validation ---
if not PRIVATE_KEY:
    raise RuntimeError("PRIVATE_KEY is not set in .env")
if not RPC_URL:
    raise RuntimeError("RPC_URL is not set in .env")
if not FACTORY_ADDRESS_RAW:
    raise RuntimeError("FACTORY_ADDRESS is not set in .env")
if not MASTER_ADDRESS_RAW:
    raise RuntimeError("MASTER_ADDRESS is not set in .env")
if not SESSION_SECRET:
    raise RuntimeError("SESSION_SECRET is not set in .env")

CHAIN_ID = int(CHAIN_ID_RAW)

# --- Address checksum ---
FACTORY_ADDRESS = Web3.to_checksum_address(FACTORY_ADDRESS_RAW)
MASTER_ADDRESS = Web3.to_checksum_address(MASTER_ADDRESS_RAW)


# --- Logging ---
def setup_logging(name: str = "gateway_app") -> logging.Logger:
    """Return a logger with a rotating file handler and a console handler.

    Idempotent: if the logger already has handlers, it returns immediately.
    """
    LOGS_DIR.mkdir(exist_ok=True)
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(LOG_LEVEL)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = RotatingFileHandler(
        LOGS_DIR / f"{name}.log",
        maxBytes=5_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    return logger


# --- ABIs ---
def load_abi(name: str) -> list:
    """
    Load an ABI from app/abis/<name>.json.

    Tolerant: if the file is missing, empty or invalid, returns [] and
    logs a warning. Startup is not affected.
    """
    log = logging.getLogger("gateway.config")
    path = ABIS_DIR / f"{name}.json"
    if not path.exists():
        log.warning(f"ABI not found at {path}. Returning [].")
        return []

    raw = path.read_text(encoding="utf-8").strip()
    if not raw or raw in ("{}", "[]"):
        log.warning(f"ABI is empty at {path}. Please paste the real ABI.")
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        log.warning(f"Invalid ABI at {path}: {e}. Returning [].")
        return []

    if not isinstance(data, list):
        log.warning(f"ABI at {path} is not a list. Returning [].")
        return []

    return data


FACTORY_ABI = load_abi("factory")
FORWARDED_ABI = load_abi("forwarded")


# --- Web3 on demand ---
def get_web3(rpc_url: str | None = None) -> Web3:
    """Create a Web3 instance. If no URL is given, use RPC_URL from .env."""
    url = rpc_url or RPC_URL
    w3 = Web3(Web3.HTTPProvider(url))
    if not w3.is_connected():
        raise RuntimeError(f"Could not connect to RPC: {url}")
    return w3


def get_factory_contract(w3: Web3, factory_address: str):
    """Instantiate the Factory contract. Requires a non-empty FACTORY_ABI."""
    if not FACTORY_ABI:
        raise RuntimeError(
            "FACTORY_ABI is empty. Paste the real ABI at app/abis/factory.json."
        )
    return w3.eth.contract(
        address=Web3.to_checksum_address(factory_address),
        abi=FACTORY_ABI,
    )

