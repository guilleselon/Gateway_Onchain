# Gateway_Onchain: Non-Custodial Payment Gateway with CREATE2 (Vyper)

[![Vyper](https://img.shields.io/badge/Vyper-^0.4.3-blue)](https://vyperlang.org/)

System for generating ephemeral payment addresses for Ethereum.  
Each address is linked to an `order_id` and automatically forwards any received funds to the merchant's wallet (`processor`).

---

## 🚀 What do these contracts do?

The system consists of two Vyper contracts:

1. **Factory**
   - Deploys minimalist proxies (EIP‑1167) using `create_minimal_proxy_to` with a deterministic `_salt`.
   - Upon deployment, it initializes the proxy with `order_id` and `processor` (the `sender` is no longer used).
   - Emits a `ProxyCreated(proxy, processor)` event.

2. **Master**
   - Immutably stores: `order_id` and `processor`.
   - In its `initialize()`:
     - Runs **only once** when the proxy is deployed.
     - If the contract already has a balance (`self.balance > 0`), it **forwards that entire balance** to the `processor` and emits `payed(processor, order_id, amount)`.
   - In its `__default__` (receiving ETH after initialization):
     - Forwards 100% of the balance to the `processor` via `raw_call`.
     - Uses **dynamic gas**: `msg.gas - 10000`.
     - Emits `payed(processor, order_id, amount)`.
   - Includes a reentrancy lock (`_locked`).

---

## 🔄 Real‑world workflow (without detailed backend)

1. **The merchant** computes the proxy address using `CREATE2` with a `salt` derived from the `order_id` (without deploying it).
2. **The merchant** shows that address to the customer as the payment destination.
3. **The customer** sends ETH to that address **before the proxy is deployed**.
   - The funds are held at that address (the account exists but has no code).
4. **The off‑chain system** (backend) detects the incoming transaction.
5. **The system** deploys the proxy by calling `Factory.create(order_id, processor, salt)`.
   - During initialisation, the proxy forwards the accumulated balance to the `processor` and emits `payed`.
6. **The system** listens for the `payed` event to associate the payment with the `order_id`.
7. **If the amount is insufficient**, the customer can send more ETH to the same address (the proxy is already deployed and its `__default__` will forward it and emit another `payed`).

> ⚡ **Key point**: The proxy is only deployed if there is an actual payment, saving gas on orders that are never executed.

---

## ✨ Key features

- **Non‑custodial**: Funds are never held; they flow directly to the `processor` as soon as the proxy is initialised or receives funds.
- **Deploy‑on‑demand**: The proxy is created only when there are funds to forward.
- **Deterministic addresses**: The address can be pre‑calculated without deployment.
- **Clear events**: `payed` includes `order_id` and `amount`; the backend can sum multiple payments for the same order.
- **Smart gas handling**: `msg.gas - 10000` avoids failures due to fixed limits.
- **Reentrancy protection**: Uses `_locked`.

---

## ⛔ Important limitations

| Limitation | Detail |
|------------|---------|
| **Native ETH only** | Does not support ERC‑20 tokens. |
| **Dynamic gas** | If the `processor` consumes a lot of gas, the 10,000 margin may be insufficient. |
| **No expiration** | Once deployed, the proxy accepts payments indefinitely. |
| **No fees** | The entire amount goes to the `processor`; there is no fee mechanism. |
| **No automatic refunds** | Cannot issue on‑chain refunds from the proxy. |
| **No KYC/AML** | Anonymous; compliance must be handled off‑chain. |

---

## 🔒 Security

- The `processor` is immutable after initialisation.
- The proxy uses `_locked` to prevent reentrancy.
- `initialize()` can only be called once.
- **Recommendation**: Use an **EOA** as the `processor` to avoid excessive gas risks.

---

## 🛠️ Technologies

- **Vyper** `^0.4.3`
- **EIP‑1167** (Minimal Proxy)
- **CREATE2** for deterministic addresses

---

## 📄 License

MIT
