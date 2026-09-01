# @version 0.4.3

interface I:
	def initialize(_order_id: uint256, _processor: address): nonpayable

event ProxyCreated:

	proxy: indexed(address)
	processor: address

master: public(address)

@deploy
def __init__(_master: address):
	self.master = _master

@external
def create(_order_id: uint256, _processor: address, _salt: bytes32) -> address:
	proxy: address = create_minimal_proxy_to(self.master, salt=_salt)
	extcall I(proxy).initialize(_order_id,_processor)
	log ProxyCreated(proxy=proxy,processor=_processor)
	return proxy
