# @version 0.4.3

event payed:
	processor: indexed(address)
	order_id: uint256
	amount: uint256

order_id: public(uint256)
processor: public(address)
initialized: public(bool)
_locked: bool

@external
def initialize(_order_id: uint256, _processor: address):
	assert not self.initialized, "initialized"
	self.order_id = _order_id
	self.processor = _processor
	if self.balance > 0:
		raw_call(self.processor,b"",value=self.balance,gas=msg.gas - 10000,revert_on_failure=True)
		log payed(processor=self.processor,order_id=self.order_id,amount=self.balance)
	self.initialized = True

@external
@payable
def __default__():
	assert self.initialized,"not initialized"
	assert not self._locked
	self._locked = True
	raw_call(self.processor,b"",value=self.balance,gas=msg.gas - 10000,revert_on_failure=True)
	log payed(processor=self.processor,order_id=self.order_id,amount=self.balance)
	self._locked = False
