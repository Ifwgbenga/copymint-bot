#!/usr/bin/env python3
"""Watch wallets. When one mints an NFT, mint the same thing from yours.

Works on every chain in CHAINS. If a minter cannot pay the mint price and
gas on that chain, funds are swapped or bridged in with LI.FI first.

Signed allowlist mints cannot be copied: the signature is locked to the
watched wallet. A copied call that reverts is skipped.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from eth_account import Account
from eth_utils import keccak, to_checksum_address

TRANSFER = "0x" + keccak(text="Transfer(address,address,uint256)").hex()
TRANSFER_SINGLE = "0x" + keccak(text="TransferSingle(address,address,address,uint256,uint256)").hex()
ZERO_TOPIC = "0x" + "0" * 64
LIFI = "https://li.quest/v1/quote"

# token address, decimals. Native is the zero address.
STABLES = {
    1: [("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", 6), ("0xdAC17F958D2ee523a2206206994597C13D831ec7", 6)],
    10: [("0x0b2C639c533813f4Aa9D7837CAf62653d097Ff85", 6), ("0x94b008aA00579c1307B0EF2c499aD98a8ce58e58", 6)],
    56: [("0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", 18), ("0x55d398326f99059fF775485246999027B3197955", 18)],
    137: [("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", 6), ("0xc2132D05D31c914a87C6611C10748AEb04B58e8F", 6)],
    8453: [("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", 6)],
    42161: [("0xaf88d065e77c8cC2239327C5EDb3A432268e5831", 6), ("0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9", 6)],
    999: [("0xb88339CB7199b77E23DB6E890353E22632Ba630f", 6)],
}

CHAINS = {
    1: ("Ethereum", "https://ethereum.publicnode.com"),
    10: ("Optimism", "https://optimism.publicnode.com"),
    56: ("BNB Chain", "https://bsc.publicnode.com"),
    137: ("Polygon", "https://polygon-bor.publicnode.com"),
    8453: ("Base", "https://base.publicnode.com"),
    42161: ("Arbitrum", "https://arbitrum.publicnode.com"),
    999: ("HyperEVM", "https://rpc.hyperliquid.xyz/evm"),
    4663: ("Robinhood", "https://rpc.mainnet.chain.robinhood.com"),
}


def env(name, default=""):
    return os.environ.get(name, default).strip()


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def as_int(value):
    if isinstance(value, int):
        return value
    text = str(value)
    return int(text, 16) if text.startswith("0x") else int(text)


@dataclass
class Chain:
    id: int
    name: str
    rpc: str


class RPC:
    def __init__(self, chains: dict[int, Chain]):
        self.chains = chains

    def call(self, chain_id, method, params, timeout=25):
        url = self.chains[chain_id].rpc
        req = urllib.request.Request(
            url,
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
            headers={"content-type": "application/json", "user-agent": "copymint-bot"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode()[:240]
            raise RuntimeError(f"{method} {exc.code} {detail}") from exc
        if body.get("error"):
            raise RuntimeError(body["error"])
        return body["result"]


def load_keys(raw):
    out = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.startswith("0x"):
            part = "0x" + part
        out.append(Account.from_key(part))
    return out


def word(addr):
    return addr[2:].lower().rjust(64, "0")


def rewrite(data, old, new):
    if old.lower() == new.lower():
        return data
    return "0x" + data[2:].lower().replace(word(old), word(new))


class Bot:
    def __init__(self):
        watch = [to_checksum_address(a) for a in env("WATCH_ADDRESSES").split(",") if a.strip()]
        if not watch:
            raise SystemExit("Set WATCH_ADDRESSES")
        self.watch = {a.lower() for a in watch}
        self.minters = load_keys(env("MINTER_PRIVATE_KEYS"))
        if not self.minters:
            raise SystemExit("Set MINTER_PRIVATE_KEYS")
        funder = env("FUNDER_PRIVATE_KEY")
        self.funder = load_keys(funder)[0] if funder else None
        self.own = {a.address.lower() for a in self.minters}
        if self.funder:
            self.own.add(self.funder.address.lower())

        selected = [int(x) for x in env("CHAINS", "1,10,56,137,8453,42161,999,4663").split(",") if x.strip()]
        chains = {}
        for cid in selected:
            if cid not in CHAINS:
                raise SystemExit(f"Unknown chain id {cid}")
            name, default = CHAINS[cid]
            chains[cid] = Chain(cid, name, env(f"RPC_{cid}", default))
        self.rpc = RPC(chains)
        self.chains = chains
        self.max_value = int(float(env("MAX_MINT_ETH", "0.05")) * 10**18)
        self.bridge = env("ENABLE_BRIDGE", "true").lower() in ("1", "true", "yes")
        self.bridge_timeout = int(env("BRIDGE_TIMEOUT", "180"))
        self.poll = float(env("POLL_SECONDS", "0.4"))
        self.dry = env("DRY_RUN", "false").lower() in ("1", "true", "yes")
        self.state_path = Path(env("STATE_PATH", "/data/state.json"))
        if str(self.state_path).startswith("/data"):
            try:
                self.state_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                self.state_path = Path("state.json")
                log("no /data volume, using ./state.json")
        self.heads = {}
        self.seen = set()
        self._load()

    def _load(self):
        if self.state_path.exists():
            data = json.loads(self.state_path.read_text())
            self.heads = {int(k): int(v) for k, v in (data.get("heads") or {}).items()}
            self.seen = set(data.get("seen") or [])

    def _save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"heads": self.heads, "seen": list(self.seen)[-5000:]}))
        tmp.replace(self.state_path)

    def balance(self, chain_id, token, who):
        if token == "native":
            return int(self.rpc.call(chain_id, "eth_getBalance", [who, "latest"]), 16)
        data = "0x70a08231" + word(who)
        return int(self.rpc.call(chain_id, "eth_call", [{"to": token, "data": data}, "latest"]), 16)

    def gas_price(self, chain_id):
        return int(self.rpc.call(chain_id, "eth_gasPrice", []), 16)

    def send(self, chain_id, acct, to, data="0x", value=0, gas=None):
        base = self.gas_price(chain_id)
        fee = max(int(base * 2), base + 1)
        if gas is None:
            est = int(
                self.rpc.call(
                    chain_id,
                    "eth_estimateGas",
                    [{"from": acct.address, "to": to, "data": data, "value": hex(value)}],
                ),
                16,
            )
            gas = int(est * 1.3) + 20_000
        have = self.balance(chain_id, "native", acct.address)
        cost = value + gas * fee
        if have < cost:
            raise RuntimeError(f"short {have} < {cost}")
        if self.dry:
            log(f"DRY {acct.address[:8]} {self.chains[chain_id].name} value {value} gas {gas}")
            return "dry", True
        nonce = int(self.rpc.call(chain_id, "eth_getTransactionCount", [acct.address, "pending"]), 16)
        signed = acct.sign_transaction(
            {
                "chainId": chain_id,
                "nonce": nonce,
                "gas": gas,
                "maxFeePerGas": fee,
                "maxPriorityFeePerGas": min(fee, max(base, 1)),
                "to": to_checksum_address(to),
                "value": value,
                "data": data,
                "type": 2,
            }
        )
        txh = self.rpc.call(chain_id, "eth_sendRawTransaction", ["0x" + signed.raw_transaction.hex()])
        for _ in range(40):
            rec = self.rpc.call(chain_id, "eth_getTransactionReceipt", [txh])
            if rec:
                return txh, int(rec["status"], 16) == 1
            time.sleep(0.5)
        return txh, False

    def lifi_quote(self, src, dst, token, amount, sender, recipient):
        qs = urllib.parse.urlencode(
            {
                "fromChain": str(src),
                "toChain": str(dst),
                "fromToken": "0x0000000000000000000000000000000000000000" if token == "native" else token,
                "toToken": "0x0000000000000000000000000000000000000000",
                "fromAmount": str(amount),
                "fromAddress": sender,
                "toAddress": recipient,
                "slippage": "0.03",
            }
        )
        req = urllib.request.Request(LIFI + "?" + qs, headers={"accept": "application/json", "user-agent": "copymint-bot"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())

    def sized_quote(self, src, dst, token, decimals, available, sender, recipient, need):
        reserve = 0 if token != "native" else 300_000 * max(self.gas_price(src), 1) * 3
        spendable = available - reserve
        if spendable <= 0:
            return None
        probe = max(spendable // 8, 10**decimals // 1000)
        probe = min(probe, spendable)
        try:
            quote = self.lifi_quote(src, dst, token, probe, sender, recipient)
        except Exception as exc:
            log(f"quote {src}->{dst} {exc}")
            return None
        out = int((quote.get("estimate") or {}).get("toAmount") or 0)
        if out <= 0:
            return None
        if out >= need and probe > 0:
            scaled = int(probe * need / out * 1.2) + 1
            scaled = min(max(scaled, 10**decimals // 1000), spendable)
            try:
                quote = self.lifi_quote(src, dst, token, scaled, sender, recipient)
                out = int((quote.get("estimate") or {}).get("toAmount") or 0)
            except Exception as exc:
                log(f"sized quote {exc}")
                return None
        if out < need:
            return None
        return quote

    def execute_quote(self, chain_id, acct, quote, token):
        approval = (quote.get("estimate") or {}).get("approvalAddress")
        amount = int((quote.get("action") or {}).get("fromAmount") or (quote.get("estimate") or {}).get("fromAmount") or 0)
        if approval and token != "native" and amount:
            allowance = int(
                self.rpc.call(
                    chain_id,
                    "eth_call",
                    [{"to": token, "data": "0xdd62ed3e" + word(acct.address) + word(approval)}, "latest"],
                ),
                16,
            )
            if allowance < amount:
                data = "0x095ea7b3" + word(approval) + hex(amount)[2:].rjust(64, "0")
                txh, ok = self.send(chain_id, acct, token, data, 0)
                log(f"approve {'OK' if ok else 'FAIL'} {txh}")
                if not ok:
                    return False
        tx = quote.get("transactionRequest") or {}
        if not tx.get("to") or not tx.get("data"):
            return False
        value = as_int(tx.get("value") or 0)
        gas = as_int(tx.get("gasLimit") or tx.get("gas") or 0)
        txh, ok = self.send(chain_id, acct, tx["to"], tx["data"], value, int(gas * 1.2) if gas else None)
        log(f"bridge {'OK' if ok else 'FAIL'} {txh}")
        return ok

    def ensure(self, minter, chain_id, need):
        if self.balance(chain_id, "native", minter.address) >= need:
            return True
        if not self.bridge:
            return False
        before = self.balance(chain_id, "native", minter.address)
        short = need - before
        holders = [minter]
        if self.funder and self.funder.address.lower() != minter.address.lower():
            holders.append(self.funder)
        for holder in holders:
            for src, chain in self.chains.items():
                candidates = [("native", 18, self.balance(src, "native", holder.address))]
                for token, decimals in STABLES.get(src, []):
                    candidates.append((token, decimals, self.balance(src, token, holder.address)))
                for token, decimals, have in candidates:
                    if have <= 0:
                        continue
                    if src == chain_id and token == "native":
                        continue
                    quote = self.sized_quote(src, chain_id, token, decimals, have, holder.address, minter.address, short)
                    if not quote:
                        continue
                    label = "native" if token == "native" else token[:8]
                    log(f"funding {minter.address[:8]} from {chain.name} {label}")
                    if not self.execute_quote(src, holder, quote, token):
                        continue
                    deadline = time.time() + self.bridge_timeout
                    while time.time() < deadline:
                        if self.balance(chain_id, "native", minter.address) >= need:
                            return True
                        time.sleep(3)
                    log(f"bridge timed out for {minter.address[:8]}")
        return self.balance(chain_id, "native", minter.address) >= need

    def is_mint(self, chain_id, tx):
        if not tx.get("to"):
            return False
        sender = (tx.get("from") or "").lower()
        if sender not in self.watch or sender in self.own:
            return False
        value = int(tx.get("value") or "0", 16)
        if value > self.max_value:
            log(f"skip {tx['hash']} value {value / 1e18:.4f} over cap")
            return False
        rec = self.rpc.call(chain_id, "eth_getTransactionReceipt", [tx["hash"]])
        if not rec or int(rec["status"], 16) != 1:
            return False
        minted = 0
        for entry in rec["logs"]:
            topics = [t.lower() for t in entry.get("topics") or []]
            if len(topics) >= 3 and topics[0] == TRANSFER and topics[1] == ZERO_TOPIC:
                minted += 1
            elif len(topics) >= 4 and topics[0] == TRANSFER_SINGLE and topics[2] == ZERO_TOPIC:
                minted += 1
        if minted <= 0:
            return False
        return True

    def copy(self, chain_id, tx):
        key = f"{chain_id}:{tx['hash']}"
        if key in self.seen:
            return
        self.seen.add(key)
        self._save()
        sender = to_checksum_address(tx["from"])
        value = int(tx.get("value") or "0", 16)
        chain = self.chains[chain_id]
        log(f"mint by {sender} on {chain.name} -> {tx['to']} value {value / 1e18:.6f} {tx['hash']}")
        for acct in self.minters:
            data = rewrite(tx["input"], sender, acct.address)
            try:
                self.rpc.call(
                    chain_id,
                    "eth_estimateGas",
                    [{"from": acct.address, "to": tx["to"], "data": data, "value": hex(value)}],
                )
            except Exception as exc:
                log(f"skip {acct.address[:8]} simulation {exc}")
                continue
            base = self.gas_price(chain_id)
            # A copied mint is usually under 400k gas. Fund a little extra.
            need = value + 500_000 * max(int(base * 2), base + 1)
            if not self.ensure(acct, chain_id, need):
                log(f"skip {acct.address[:8]} not enough on {chain.name} and no route")
                continue
            try:
                txh, ok = self.send(chain_id, acct, tx["to"], data, value)
            except Exception as exc:
                log(f"send {acct.address[:8]} {exc}")
                continue
            log(f"{'OK' if ok else 'FAIL'} {acct.address} {txh}")

    def scan(self, chain_id):
        head = int(self.rpc.call(chain_id, "eth_blockNumber", []), 16)
        last = self.heads.get(chain_id)
        if last is None:
            self.heads[chain_id] = head
            self._save()
            log(f"{self.chains[chain_id].name} watching from {head}")
            return
        if head <= last:
            return
        start = last + 1
        if head - start > 40:
            log(f"{self.chains[chain_id].name} behind {head - start} blocks, skipping to {head - 40}")
            start = head - 40
        for number in range(start, head + 1):
            block = self.rpc.call(chain_id, "eth_getBlockByNumber", [hex(number), True])
            for tx in (block or {}).get("transactions") or []:
                if isinstance(tx, str):
                    continue
                try:
                    if self.is_mint(chain_id, tx):
                        self.copy(chain_id, tx)
                except Exception as exc:
                    log(f"tx {tx.get('hash')} {exc}")
            self.heads[chain_id] = number
        self._save()

    def run(self):
        names = ", ".join(c.name for c in self.chains.values())
        log(f"watching {len(self.watch)} wallet(s) on {names}")
        log(f"minters {', '.join(a.address for a in self.minters)}")
        if self.funder:
            log(f"funder {self.funder.address}")
        log(f"bridge {'on' if self.bridge else 'off'} dry {'on' if self.dry else 'off'}")
        while True:
            for cid in self.chains:
                try:
                    self.scan(cid)
                except Exception as exc:
                    log(f"{self.chains[cid].name} {exc}")
            time.sleep(self.poll)


if __name__ == "__main__":
    Bot().run()
