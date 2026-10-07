"""
Local nonce manager.

Tracks the next nonce to use for the gateway wallet, so multiple
transactions can be sent in the same cycle without waiting for
confirmations.

Usage:
    from app.core import nonce
    n = nonce.next_nonce(w3, account.address)
    n2 = nonce.next_nonce(w3, account.address)
    # n2 == n + 1
"""

import threading

_lock = threading.Lock()
_state: dict[str, int] = {}


def next_nonce(w3, address: str) -> int:
    """
    Return the next nonce to use and increment the local counter.

    On the first call for a given address, reads the current pending
    nonce from the RPC (not confirmed), so in-flight txs are not
    overwritten.
    """
    addr = address.lower()
    with _lock:
        if addr not in _state:
            # 'pending' includes txs not yet mined
            _state[addr] = w3.eth.get_transaction_count(address, "pending")
        n = _state[addr]
        _state[addr] = n + 1
        return n


def peek(address: str) -> int | None:
    """Return the current nonce without incrementing, or None if unknown."""
    return _state.get(address.lower())


def reset(address: str | None = None) -> None:
    """Reset the local counter. Useful on errors or after a restart."""
    with _lock:
        if address is None:
            _state.clear()
        else:
            _state.pop(address.lower(), None)
