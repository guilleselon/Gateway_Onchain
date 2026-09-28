# @version ^0.4.0

from ethereum.ercs import IERC20

USDC: constant(address) = {usdc_addr}

event Paid:
    order_id: uint256
    payer: indexed(address)
    token: indexed(address)
    amount: uint256

order_id: public(uint256)
processor: public(address)
initialized: public(bool)
_locked: bool

@deploy
def __init__():
    self.initialized = True

@external
def initialize(_order_id: uint256, _processor: address):
    assert not self.initialized, "already initialized"
    assert _processor != empty(address), "zero processor"

    self.initialized = True
    self.order_id = _order_id
    self.processor = _processor

    mon_amount: uint256 = self.balance
    if mon_amount > 0:
        raw_call(_processor, b"", value=mon_amount, gas=msg.gas - 10000, revert_on_failure=True)
        log Paid(order_id=_order_id, payer=msg.sender, token=empty(address), amount=mon_amount)

    usdc_amount: uint256 = staticcall IERC20(USDC).balanceOf(self)
    if usdc_amount > 0:
        success: bool = extcall IERC20(USDC).transfer(_processor, usdc_amount)
        assert success, "usdc transfer failed"
        log Paid(order_id=_order_id, payer=msg.sender, token=USDC, amount=usdc_amount)

@external
@payable
def __default__():
    assert self.initialized, "not initialized"
    assert not self._locked, "reentrant"
    self._locked = True

    mon_amount: uint256 = self.balance
    if mon_amount > 0:
        raw_call(self.processor, b"", value=mon_amount, gas=msg.gas - 10000, revert_on_failure=True)
        log Paid(order_id=self.order_id, payer=msg.sender, token=empty(address), amount=mon_amount)

    self._locked = False

@external
def sweep_usdc():
    assert self.initialized, "not initialized"
    amount: uint256 = staticcall IERC20(USDC).balanceOf(self)
    assert amount > 0, "nothing to sweep"
    success: bool = extcall IERC20(USDC).transfer(self.processor, amount)
    assert success, "usdc transfer failed"
    log Paid(order_id=self.order_id, payer=msg.sender, token=USDC, amount=amount)
