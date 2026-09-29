# @version ^0.4.0

interface I:
    def initialize(_order_id: uint256, _processor: address): nonpayable

event ProxyCreated:
    proxy: indexed(address)
    processor: indexed(address)
    order_id: uint256
    salt: bytes32

master: public(address)

@deploy
def __init__(_master: address):
    self.master = _master

@external
def create(_order_id: uint256, _processor: address, _salt: bytes32) -> address:
    actual_salt: bytes32 = keccak256(abi_encode(msg.sender, _salt))
    proxy: address = create_minimal_proxy_to(self.master, salt=actual_salt)
    extcall I(proxy).initialize(_order_id, _processor)
    log ProxyCreated(proxy=proxy, processor=_processor, order_id=_order_id, salt=actual_salt)
    return proxy

