"""
Deploy the Master (Forwarded) and Factory contracts on the network
configured in .env.

The Vyper source is read from contracts/Forwarded.vy and contracts/Factory.vy.

Usage:
    python scripts/deploy_contracts.py

On completion, MASTER_ADDRESS and FACTORY_ADDRESS are written back to
.env automatically, and the ABIs are saved to app/abis/.
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import vyper
from web3 import Web3

from app.config import PRIVATE_KEY, RPC_URL, setup_logging

log = setup_logging("gateway_deploy")

BASE_DIR = Path(__file__).resolve().parent.parent
CONTRACTS_DIR = BASE_DIR / "contracts"
ABIS_DIR = BASE_DIR / "app" / "abis"


# ---------------------------------------------------------------------------
# Source loading
# ---------------------------------------------------------------------------

def _load_source(filename: str) -> str:
    """Read a Vyper source file from contracts/."""
    path = CONTRACTS_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"Contract not found: {path}")
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Deployment helpers
# ---------------------------------------------------------------------------

def _deploy(w3: Web3, abi: list, bytecode: str, args: list,
            private_key: str, gas: int = 5_000_000) -> str:
    """Deploy a contract and return its address."""
    account = w3.eth.account.from_key(private_key)
    Contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    nonce = w3.eth.get_transaction_count(account.address)

    tx = Contract.constructor(*args).build_transaction({
        "from": account.address,
        "nonce": nonce,
        "gas": gas,
        "gasPrice": w3.eth.gas_price,
        "chainId": w3.eth.chain_id,
    })
    signed = w3.eth.account.sign_transaction(tx, private_key=private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    log.info(f"TX sent: {tx_hash.hex()}")
    log.info(f"  waiting for receipt (chain {w3.eth.chain_id})...")
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
    if receipt.status != 1:
        raise RuntimeError(f"TX failed: {tx_hash.hex()}")
    return receipt.contractAddress


def _update_env(key: str, value: str) -> None:
    """Update or append a variable in the root .env file."""
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        log.warning(".env not found; cannot update.")
        return
    content = env_path.read_text(encoding="utf-8")
    pattern = re.compile(rf"^{key}=.*$", re.MULTILINE)
    if pattern.search(content):
        content = pattern.sub(f"{key}={value}", content)
    else:
        content = content.rstrip() + f"\n{key}={value}\n"
    env_path.write_text(content, encoding="utf-8")
    log.info(f"  .env updated: {key}={value}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    log.info(f"Connecting to {RPC_URL}")
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        log.error("Could not connect to RPC")
        return 1

    account = w3.eth.account.from_key(PRIVATE_KEY)
    balance = w3.eth.get_balance(account.address)
    log.info(f"Wallet:  {account.address}")
    log.info(f"Balance: {w3.from_wei(balance, 'ether')} native")
    log.info(f"chain_id: {w3.eth.chain_id}")
    log.info(f"block:    {w3.eth.block_number}")

    if balance == 0:
        log.error("Wallet has no funds to pay gas.")
        log.error("Get testnet funds from the network faucet.")
        return 1

    # 1. Load sources
    log.info("Loading Vyper sources from contracts/...")
    try:
        forwarded_source = _load_source("Forwarded.vy")
        factory_source = _load_source("Factory.vy")
    except FileNotFoundError as e:
        log.error(str(e))
        return 1

    # 2. Compile
    log.info("Compiling contracts...")
    try:
        compiled_fw = vyper.compile_code(
            forwarded_source, output_formats=["bytecode", "abi"]
        )
        compiled_fc = vyper.compile_code(
            factory_source, output_formats=["bytecode", "abi"]
        )
    except Exception as e:
        log.error(f"Compilation failed: {e}")
        return 1

    log.info(f"  Forwarded bytecode: {len(compiled_fw['bytecode'])} chars")
    log.info(f"  Factory   bytecode: {len(compiled_fc['bytecode'])} chars")

    # 3. Deploy Master
    log.info("Deploying Master (Forwarded)...")
    master_addr = _deploy(
        w3, compiled_fw["abi"], compiled_fw["bytecode"], [], PRIVATE_KEY
    )
    log.info(f"  MASTER_ADDRESS = {master_addr}")

    # 4. Deploy Factory
    log.info("Deploying Factory...")
    factory_addr = _deploy(
        w3, compiled_fc["abi"], compiled_fc["bytecode"],
        [master_addr], PRIVATE_KEY,
    )
    log.info(f"  FACTORY_ADDRESS = {factory_addr}")

    # 5. Save ABIs
    ABIS_DIR.mkdir(exist_ok=True)
    (ABIS_DIR / "factory.json").write_text(
        json.dumps(compiled_fc["abi"], indent=2), encoding="utf-8"
    )
    (ABIS_DIR / "forwarded.json").write_text(
        json.dumps(compiled_fw["abi"], indent=2), encoding="utf-8"
    )
    log.info(f"ABIs saved to {ABIS_DIR}")

    # 6. Update .env
    log.info("Updating .env...")
    _update_env("MASTER_ADDRESS", master_addr)
    _update_env("FACTORY_ADDRESS", factory_addr)

    log.info("")
    log.info("=" * 60)
    log.info("  DEPLOYMENT COMPLETE")
    log.info("=" * 60)
    log.info(f"  MASTER_ADDRESS  = {master_addr}")
    log.info(f"  FACTORY_ADDRESS = {factory_addr}")
    log.info("")
    log.info("  Next step: restart the worker and the app.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

