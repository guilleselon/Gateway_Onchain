"""
Blockchain access layer for the gateway.

Functions:
- calculate_proxy(): deterministic CREATE2 proxy address (call, no gas).
- deploy_proxy(): send the deployment transaction to the factory.
- wait_for_confirmations(): block until N confirmations or timeout.
- get_balance(): native balance of an address, in wei.
- get_paid_events(): search Paid events in a block range.

All functions receive the Web3 instance and the factory contract as
parameters, so they are not tied to a specific chain (the gateway is
multi-chain).
"""

import time

from web3 import Web3

from app.config import (
    FORWARDED_ABI,
    FACTORY_ABI,
    get_factory_contract,
    get_web3,
    setup_logging,
)

log = setup_logging("gateway_blockchain")


# ---------------------------------------------------------------------------
# Address calculation and deployment
# ---------------------------------------------------------------------------

def calculate_proxy(w3: Web3, factory_contract, order_id: int,
                    processor: str, salt_hex: str) -> str:
    """
    Calculate the proxy address with CREATE2 WITHOUT deploying it.

    Uses eth_call: it simulates the full execution of create() in the EVM,
    including the create_minimal_proxy_to call and the proxy's initialize.
    Because it is a simulation, nothing is persisted and no gas is spent.
    Since CREATE2 is deterministic, the returned value is exactly the
    address the proxy will have once actually deployed.

    Args:
        w3: Web3 instance.
        factory_contract: instantiated factory contract.
        order_id: uint256.
        processor: destination address for the forwarded funds.
        salt_hex: bytes32 in hex (0x...).

    Returns:
        Checksum proxy address.
    """
    salt_bytes = _hex_to_bytes32(salt_hex)
    return factory_contract.functions.create(
        order_id,
        Web3.to_checksum_address(processor),
        salt_bytes,
    ).call()


def deploy_proxy(w3: Web3, factory_contract, order_id: int,
                 processor: str, salt_hex: str, private_key: str,
                 gas_limit: int = 500_000) -> str:
    """
    Deploy the proxy by sending a transaction to the factory.

    The gateway pays the gas. The proxy, on initialization, forwards its
    entire balance to the processor and emits the Paid event.

    The nonce is managed by app.core.nonce so multiple transactions can
    be sent in the same cycle without waiting for confirmations.

    Args:
        w3: Web3 instance.
        factory_contract: factory contract.
        order_id: uint256.
        processor: destination address.
        salt_hex: bytes32 in hex.
        private_key: gateway private key (with 0x prefix).
        gas_limit: gas limit. 500k is comfortable for a minimal proxy.

    Returns:
        Transaction hash as a hex string.
    """
    from app.core import nonce as nonce_manager

    account = w3.eth.account.from_key(private_key)
    nonce = nonce_manager.next_nonce(w3, account.address)
    salt_bytes = _hex_to_bytes32(salt_hex)

    tx = factory_contract.functions.create(
        order_id,
        Web3.to_checksum_address(processor),
        salt_bytes,
    ).build_transaction({
        "from": account.address,
        "nonce": nonce,
        "gas": gas_limit,
        "gasPrice": w3.eth.gas_price,
        "chainId": w3.eth.chain_id,
    })

    signed = w3.eth.account.sign_transaction(tx, private_key=private_key)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    log.info(f"Proxy deploy tx sent. nonce={nonce} tx={tx_hash.hex()}")
    return tx_hash.hex()


def wait_for_confirmations(w3: Web3, tx_hash_hex: str,
                           confirmations: int = 5,
                           timeout: int = 300) -> object | None:
    """
    Block until the transaction has at least N confirmations or the
    timeout expires.

    Kept for compatibility with scripts that do want to block.
    The worker does NOT use this anymore; it checks receipts in a
    separate cycle.

    Returns:
        The receipt if confirmed, None on timeout.
    """
    start = time.time()
    receipt = None
    while time.time() - start < timeout:
        try:
            receipt = w3.eth.get_transaction_receipt(tx_hash_hex)
            if receipt is not None:
                current = w3.eth.block_number - receipt.blockNumber
                if current >= confirmations:
                    return receipt
        except Exception:
            pass
        time.sleep(3)
    return receipt


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def get_balance(w3: Web3, address: str) -> int:
    """Native balance of an address, in wei."""
    return w3.eth.get_balance(Web3.to_checksum_address(address))


def get_paid_events(w3: Web3, from_block: int, to_block: int | str,
                    proxy_addresses: list[str] | None = None) -> list[dict]:
    """
    Search for Paid events emitted by proxies.

    Paid event fields:
      - order_id (uint256, not indexed)
      - payer    (address, indexed)
      - amount   (uint256, not indexed)

    Args:
        w3: Web3 instance.
        from_block: starting block.
        to_block: ending block, or 'latest'.
        proxy_addresses: optional list to filter by proxy address.

    Returns:
        List of dicts with tx_hash, log_index, block_number, proxy,
        order_id, payer, amount.
    """
    if not FORWARDED_ABI:
        log.warning("FORWARDED_ABI is empty; cannot search events.")
        return []

    # Generic instance used only for event decoding
    forwarded = w3.eth.contract(abi=FORWARDED_ABI)

    event_filter = {
        "fromBlock": from_block,
        "toBlock": to_block,
        "topics": [forwarded.events.Paid().topic],
    }
    if proxy_addresses:
        event_filter["address"] = [
            Web3.to_checksum_address(a) for a in proxy_addresses
        ]

    logs = w3.eth.get_logs(event_filter)

    events = []
    for entry in logs:
        try:
            decoded = forwarded.events.Paid().process_log(entry)
            events.append({
                "tx_hash": decoded["transactionHash"].hex(),
                "log_index": decoded["logIndex"],
                "block_number": decoded["blockNumber"],
                "proxy": decoded["address"],
                "order_id": decoded["args"]["order_id"],
                "payer": decoded["args"]["payer"],
                "amount": decoded["args"]["amount"],
            })
        except Exception as e:
            log.warning(f"Failed to decode log: {e}")

    return events


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _hex_to_bytes32(salt_hex: str) -> bytes:
    """
    Convert a hex salt (with or without 0x) to bytes32.
    """
    s = salt_hex[2:] if salt_hex.startswith("0x") else salt_hex
    if len(s) != 64:
        raise ValueError(f"Salt must be 32 bytes (64 hex chars), got {len(s)}")
    return bytes.fromhex(s)

