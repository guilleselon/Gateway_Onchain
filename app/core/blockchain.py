"""
Blockchain access layer for the gateway.
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


def _to_0x(value) -> str:
    if hasattr(value, "to_0x_hex"):
        return value.to_0x_hex()
    if hasattr(value, "hex"):
        h = value.hex()
        return h if h.startswith("0x") else "0x" + h
    s = str(value)
    return s if s.startswith("0x") else "0x" + s


def calculate_proxy(w3: Web3, factory_contract, order_id: int,
                    processor: str, salt_hex: str,
                    sender_address: str) -> str:
    """
    Calculate the proxy address with CREATE2 WITHOUT deploying it.

    The factory computes actual_salt = keccak256(abi_encode(msg.sender, _salt)),
    so the resulting address depends on the caller. The simulation MUST use
    the same `from` that the real deploy will use.
    """
    if not sender_address:
        raise ValueError("sender_address is required (matches msg.sender)")

    salt_bytes = _hex_to_bytes32(salt_hex)
    return factory_contract.functions.create(
        order_id,
        Web3.to_checksum_address(processor),
        salt_bytes,
    ).call({"from": Web3.to_checksum_address(sender_address)})


def deploy_proxy(w3: Web3, factory_contract, order_id: int,
                 processor: str, salt_hex: str, private_key: str,
                 gas_limit: int = 500_000) -> str:
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
    tx_hash_hex = _to_0x(tx_hash)
    log.info(f"Proxy deploy tx sent. nonce={nonce} tx={tx_hash_hex}")
    return tx_hash_hex


def wait_for_confirmations(w3: Web3, tx_hash_hex: str,
                           confirmations: int = 5,
                           timeout: int = 300) -> object | None:
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


def get_balance(w3: Web3, address: str) -> int:
    return w3.eth.get_balance(Web3.to_checksum_address(address))


def has_code(w3: Web3, address: str) -> bool:
    """True if the address has bytecode deployed (it's a contract)."""
    code = w3.eth.get_code(Web3.to_checksum_address(address))
    return len(code) > 0


def get_paid_events(w3: Web3, from_block: int, to_block: int | str,
                    proxy_addresses: list[str] | None = None) -> list[dict]:
    if not FORWARDED_ABI:
        log.warning("FORWARDED_ABI is empty; cannot search events.")
        return []

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
                "tx_hash": _to_0x(decoded["transactionHash"]),
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


def _hex_to_bytes32(salt_hex: str) -> bytes:
    s = salt_hex[2:] if salt_hex.startswith("0x") else salt_hex
    if len(s) != 64:
        raise ValueError(f"Salt must be 32 bytes (64 hex chars), got {len(s)}")
    return bytes.fromhex(s)
