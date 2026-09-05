"""HOODBarons on-chain tracker — Solana + Robinhood Chain.

Free, keyless sources only, everything labeled as sampled heuristics:
  * DexScreener public API   → pairs, prices, 1h/24h change, liquidity, volume, launches (both chains)
  * pump.fun frontend API    → newest Solana launches
  * Solana public RPC        → pool transactions → buyer wallets; wallet token accounts + swap flow
  * Robinhood Chain RPC      → Uniswap Swap logs on winner pairs → wallets; Transfer logs → net holdings
Blockscout for Robinhood Chain is Cloudflare-gated, so the RPC is used directly.

A refresher thread recomputes top coins / new launches / wallets for both chains every
REFRESH_MIN minutes and stores JSON in the shared sqlite cache; the API serves the cache.
"""
from __future__ import annotations

import json
import math
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime

DEX = "https://api.dexscreener.com"
PUMP = "https://frontend-api-v3.pump.fun/coins?offset=0&limit=40&sort=created_timestamp&order=DESC&includeNsfw=false"
SOL_RPC = "https://api.mainnet-beta.solana.com"
RH_RPC = "https://rpc.mainnet.chain.robinhood.com"
RH_CHAIN_ID = 4663
WSOL = "So11111111111111111111111111111111111111112"
TOPIC_SWAP_V3 = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
TOPIC_SWAP_V2 = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
TOPIC_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
REFRESH_MIN = 20
CHAINS = ("solana", "robinhood")
UA = "HOODBarons/1.0 (+https://prometheus7.com/HOODBarons)"

_state: dict[str, dict] = {}       # in-memory latest results per (chain, kind)
_lock = threading.Lock()
_log = lambda *a: print("[chain]", *a, flush=True)  # noqa: E731


# ------------------------------------------------------------------ http helpers

def _get(url: str, timeout: int = 25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _rpc(url: str, method: str, params, timeout: int = 30, retries: int = 3):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for i in range(retries):
        try:
            req = urllib.request.Request(url, data=body, headers={"User-Agent": UA, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                j = json.loads(r.read())
            if "error" in j and j["error"]:
                if "429" in str(j["error"]) or "rate" in str(j["error"]).lower():
                    time.sleep(1.5 * (i + 1))
                    continue
                return None
            return j.get("result")
        except urllib.error.HTTPError as e:  # noqa: PERF203
            if e.code == 429:
                time.sleep(2.0 * (i + 1))
                continue
            return None
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    return None


def _age(ts_ms: float | None) -> str:
    if not ts_ms:
        return "—"
    s = max(0.0, time.time() - ts_ms / 1000.0)
    if s < 3600:
        return f"{s / 60:.0f}m"
    if s < 86400:
        return f"{s / 3600:.1f}h"
    return f"{s / 86400:.1f}d"


def _f(v, d=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


# ------------------------------------------------------------------ DexScreener

def _pair_row(p: dict) -> dict:
    pc, vol, liq = p.get("priceChange") or {}, p.get("volume") or {}, p.get("liquidity") or {}
    tx = (p.get("txns") or {}).get("h24") or {}
    return {"chain": p.get("chainId"), "address": p["baseToken"]["address"], "symbol": p["baseToken"].get("symbol", "?"),
            "name": p["baseToken"].get("name", ""), "price": _f(p.get("priceUsd")), "h1": _f(pc.get("h1")), "h6": _f(pc.get("h6")),
            "h24": _f(pc.get("h24")), "volume24": _f(vol.get("h24"), 0.0), "liquidity": _f(liq.get("usd"), 0.0),
            "mcap": _f(p.get("marketCap")) or _f(p.get("fdv")), "pair": p.get("pairAddress"), "dex": p.get("dexId"),
            "url": p.get("url"), "created": p.get("pairCreatedAt"), "age": _age(p.get("pairCreatedAt")),
            "buys24": tx.get("buys"), "sells24": tx.get("sells"), "quote": (p.get("quoteToken") or {}).get("address"),
            "quote_usd": (_f(p.get("priceUsd"), 0.0) / _f(p.get("priceNative"), 1.0)) if _f(p.get("priceNative")) else None}


def candidate_tokens(chain: str) -> dict[str, dict]:
    """Union of DexScreener discovery surfaces for one chain → {token_address: best pair row}."""
    addrs: set[str] = set()
    for path in ("/token-profiles/latest/v1", "/token-boosts/top/v1", "/token-boosts/latest/v1"):
        try:
            for x in _get(DEX + path):
                if x.get("chainId") == chain:
                    addrs.add(x["tokenAddress"])
        except Exception as e:  # noqa: BLE001
            _log("dex", path, e)
    queries = ["SOL", "USDC", "pump", "WSOL", "USDT", "AI", "trump", "cat", "dog"] if chain == "solana" else ["ETH", "USDC", "WETH", "stock", "robinhood", "USDT", "HOOD", "AI"]
    best: dict[str, dict] = {}
    for q in queries:
        try:
            for p in _get(DEX + "/latest/dex/search?q=" + urllib.parse.quote(q)).get("pairs") or []:
                if p.get("chainId") == chain:
                    row = _pair_row(p)
                    if row["liquidity"] > best.get(row["address"], {}).get("liquidity", -1):
                        best[row["address"]] = row
        except Exception as e:  # noqa: BLE001
            _log("dex search", q, e)
    addrs -= set(best)
    addrs = list(addrs)[:150]
    for i in range(0, len(addrs), 30):
        try:
            for p in _get(f"{DEX}/tokens/v1/{chain}/{','.join(addrs[i:i + 30])}") or []:
                row = _pair_row(p)
                if row["liquidity"] > best.get(row["address"], {}).get("liquidity", -1):
                    best[row["address"]] = row
        except Exception as e:  # noqa: BLE001
            _log("dex tokens", e)
    return best


def top_coins(chain: str, cands: dict[str, dict] | None = None) -> dict:
    cands = cands or candidate_tokens(chain)
    rows = [r for r in cands.values() if r["h24"] is not None and r["liquidity"] >= 5_000 and r["volume24"] >= 10_000 and r["price"]]
    for r in rows:
        # profit score: 24h gain weighted by how much money actually traded it
        r["score"] = (r["h24"] or 0) * math.log10(max(r["volume24"], 10.0))
    rows.sort(key=lambda r: -r["score"])
    return {"chain": chain, "items": rows[:25], "candidates": len(cands), "updated": datetime.utcnow().isoformat() + "Z",
            "note": "Ranked by 24h gain × log(24h volume); liquidity ≥ $5k and volume ≥ $10k. Live from DexScreener."}


def new_coins(chain: str, cands: dict[str, dict] | None = None) -> dict:
    cands = cands or candidate_tokens(chain)
    items = []
    if chain == "solana":
        try:
            for cn in _get(PUMP) or []:
                items.append({"chain": chain, "address": cn.get("mint"), "symbol": cn.get("symbol", "?"), "name": cn.get("name", ""),
                              "created": cn.get("created_timestamp"), "age": _age(cn.get("created_timestamp")),
                              "mcap": _f(cn.get("usd_market_cap")), "liquidity": None, "graduated": bool(cn.get("complete")),
                              "source": "pump.fun", "url": "https://pump.fun/coin/" + str(cn.get("mint"))})
        except Exception as e:  # noqa: BLE001
            _log("pump", e)
    dex_new = sorted([r for r in cands.values() if r.get("created")], key=lambda r: -r["created"])[:25]
    for r in dex_new:
        items.append({**r, "source": "dex " + str(r.get("dex"))})
    items.sort(key=lambda r: -(r.get("created") or 0))
    return {"chain": chain, "items": items[:40], "updated": datetime.utcnow().isoformat() + "Z",
            "note": "pump.fun mints (Solana) + newest DEX pairs. Most new launches go to zero; this is a radar, not a list of buys."}


# ------------------------------------------------------------------ Solana wallets

def _sol_tx(sig: str):
    return _rpc(SOL_RPC, "getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])


def _token_deltas(tx: dict, owner: str) -> dict[str, float]:
    """Net uiAmount change per mint for one owner in a transaction."""
    meta = tx.get("meta") or {}
    pre = {(b["mint"], b.get("owner")): _f((b.get("uiTokenAmount") or {}).get("uiAmount"), 0.0) for b in meta.get("preTokenBalances") or []}
    post = {(b["mint"], b.get("owner")): _f((b.get("uiTokenAmount") or {}).get("uiAmount"), 0.0) for b in meta.get("postTokenBalances") or []}
    out: dict[str, float] = {}
    for (mint, o) in set(pre) | set(post):
        if o == owner:
            d = post.get((mint, o), 0.0) - pre.get((mint, o), 0.0)
            if abs(d) > 0:
                out[mint] = out.get(mint, 0.0) + d
    return out


def _sol_delta(tx: dict, owner: str) -> float:
    """Net SOL change (in SOL) for the owner's fee-payer account."""
    meta, msg = tx.get("meta") or {}, (tx.get("transaction") or {}).get("message") or {}
    keys = msg.get("accountKeys") or []
    for i, k in enumerate(keys):
        pk = k.get("pubkey") if isinstance(k, dict) else k
        if pk == owner:
            pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
            if i < len(pre) and i < len(post):
                return (post[i] - pre[i]) / 1e9
    return 0.0


def sol_buyers(winners: list[dict], per_pool: int = 8, budget_s: float = 240.0) -> dict[str, dict]:
    wallets: dict[str, dict] = {}
    t0 = time.time()
    for w in winners:
        pool = w.get("pair")
        if not pool or time.time() - t0 > budget_s:
            continue
        sigs = _rpc(SOL_RPC, "getSignaturesForAddress", [pool, {"limit": 30}]) or []
        seen = 0
        for s in sigs:
            if s.get("err") or seen >= per_pool or time.time() - t0 > budget_s:
                continue
            tx = _sol_tx(s["signature"])
            if not tx:
                continue
            keys = tx["transaction"]["message"]["accountKeys"]
            signer = keys[0]["pubkey"] if isinstance(keys[0], dict) else keys[0]
            d = _token_deltas(tx, signer).get(w["address"], 0.0)
            seen += 1
            if d > 0:
                rec = wallets.setdefault(signer, {"address": signer, "chain": "solana", "winners": 0, "score": 0.0, "tokens": [], "buys": []})
                if w["symbol"] not in rec["tokens"]:
                    rec["tokens"].append(w["symbol"])
                    rec["winners"] += 1
                    rec["score"] += 1.0 + min(max((w.get("h24") or 0) / 100.0, 0.0), 5.0)
                rec["buys"].append({"token": w["symbol"], "amount": d, "time": s.get("blockTime")})
            time.sleep(0.12)
        _log("solana pool sampled", w["symbol"], "txs", seen, "wallets so far", len(wallets), f"{time.time() - t0:.0f}s")
    return wallets


def sol_wallet(address: str, price_of: dict[str, float] | None = None, sample: int = 20) -> dict:
    """Holdings (token accounts × DexScreener price) + SOL flow over the last `sample` transactions."""
    price_of = dict(price_of or {})
    accts = _rpc(SOL_RPC, "getTokenAccountsByOwner", [address, {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"}, {"encoding": "jsonParsed"}]) or {}
    positions: dict[str, dict] = {}
    for a in accts.get("value") or []:
        info = a["account"]["data"]["parsed"]["info"]
        amt = _f((info.get("tokenAmount") or {}).get("uiAmount"), 0.0)
        if amt > 0:
            positions[info["mint"]] = {"mint": info["mint"], "amount": amt, "net_flow": 0.0}
    mints = [m for m in positions if m not in price_of][:60]
    for i in range(0, len(mints), 30):
        try:
            for p in _get(f"{DEX}/tokens/v1/solana/{','.join(mints[i:i + 30])}") or []:
                addr = p["baseToken"]["address"]
                if addr not in price_of or (_f((p.get("liquidity") or {}).get("usd"), 0) > 0 and price_of.get(addr) is None):
                    price_of[addr] = _f(p.get("priceUsd"))
                    positions.setdefault(addr, {"mint": addr, "amount": 0.0, "net_flow": 0.0})["symbol"] = p["baseToken"].get("symbol")
        except Exception as e:  # noqa: BLE001
            _log("wallet prices", e)
    sol_price = price_of.get(WSOL) or _sol_price()
    sigs = _rpc(SOL_RPC, "getSignaturesForAddress", [address, {"limit": sample}]) or []
    flow_sol, swaps = 0.0, 0
    for s in sigs:
        if s.get("err"):
            continue
        tx = _sol_tx(s["signature"])
        if not tx:
            continue
        deltas = _token_deltas(tx, address)
        if not deltas:
            continue
        swaps += 1
        dsol = _sol_delta(tx, address)
        flow_sol += dsol
        for mint, d in deltas.items():
            positions.setdefault(mint, {"mint": mint, "amount": 0.0, "net_flow": 0.0})["net_flow"] += dsol * sol_price / max(len(deltas), 1)
        time.sleep(0.12)
    holdings = 0.0
    for m, p in positions.items():
        pr = price_of.get(m)
        p["price"] = pr
        p["value"] = (pr or 0.0) * p["amount"] if pr else None
        holdings += p["value"] or 0.0
    plist = sorted(positions.values(), key=lambda p: -(p.get("value") or 0.0))
    return {"address": address, "chain": "solana", "holdings": holdings, "pnl": flow_sol * sol_price, "flow_sol": flow_sol, "swaps": swaps,
            "positions": plist[:25], "sol_price": sol_price,
            "note": f"Sampled last {sample} txs on the public RPC; flow PnL = net SOL moved in swaps × SOL price; holdings priced by DexScreener where a market exists."}


def _sol_price() -> float:
    try:
        p = _get(f"{DEX}/tokens/v1/solana/{WSOL}")
        return _f(p[0].get("priceUsd"), 0.0) if p else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


# ------------------------------------------------------------------ Robinhood Chain wallets

def _eth_call(to: str, data: str):
    return _rpc(RH_RPC, "eth_call", [{"to": to, "data": data}, "latest"])


def rh_decimals(token: str) -> int:
    r = _eth_call(token, "0x313ce567")
    try:
        return int(r, 16) if r else 18
    except ValueError:
        return 18


def rh_buyers(winners: list[dict], per_pair: int = 15, blocks: int = 400_000, budget_s: float = 180.0) -> dict[str, dict]:
    """Wallets that swapped on the winners' pairs recently (tx.from of Swap logs)."""
    wallets: dict[str, dict] = {}
    head = _rpc(RH_RPC, "eth_blockNumber", [])
    if not head:
        return wallets
    head = int(head, 16)
    t0 = time.time()
    for w in winners:
        pair = w.get("pair")
        if not pair or time.time() - t0 > budget_s:
            continue
        logs = _rpc(RH_RPC, "eth_getLogs", [{"fromBlock": hex(max(0, head - blocks)), "toBlock": hex(head), "address": pair,
                                            "topics": [[TOPIC_SWAP_V3, TOPIC_SWAP_V2]]}]) or []
        for lg in logs[-per_pair:]:
            tx = _rpc(RH_RPC, "eth_getTransactionByHash", [lg["transactionHash"]])
            if not tx:
                continue
            frm = (tx.get("from") or "").lower()
            if not frm:
                continue
            rec = wallets.setdefault(frm, {"address": frm, "chain": "robinhood", "winners": 0, "score": 0.0, "tokens": [], "buys": []})
            if w["symbol"] not in rec["tokens"]:
                rec["tokens"].append(w["symbol"])
                rec["winners"] += 1
                rec["score"] += 1.0 + min(max((w.get("h24") or 0) / 100.0, 0.0), 5.0)
            rec["buys"].append({"token": w["symbol"], "block": int(lg["blockNumber"], 16)})
            time.sleep(0.05)
    return wallets


def rh_wallet(address: str, tokens: list[dict], blocks: int = 400_000) -> dict:
    """Net holdings of the tracked tokens from Transfer logs + live balances; values at DexScreener prices."""
    address = address.lower()
    topic_addr = "0x" + address[2:].rjust(64, "0")
    head = int(_rpc(RH_RPC, "eth_blockNumber", []) or "0x0", 16)
    positions, swaps, flow_usd = [], 0, 0.0
    for t in tokens[:12]:
        tok = t["address"]
        dec = rh_decimals(tok)
        bal = _eth_call(tok, "0x70a08231" + address[2:].rjust(64, "0"))
        amount = (int(bal, 16) / 10 ** dec) if bal and bal != "0x" else 0.0
        inn = _rpc(RH_RPC, "eth_getLogs", [{"fromBlock": hex(max(0, head - blocks)), "toBlock": hex(head), "address": tok, "topics": [TOPIC_TRANSFER, None, topic_addr]}]) or []
        out = _rpc(RH_RPC, "eth_getLogs", [{"fromBlock": hex(max(0, head - blocks)), "toBlock": hex(head), "address": tok, "topics": [TOPIC_TRANSFER, topic_addr]}]) or []
        net = (sum(int(l["data"], 16) for l in inn) - sum(int(l["data"], 16) for l in out)) / 10 ** dec
        swaps += len(inn) + len(out)
        price = t.get("price")
        value = amount * price if price else None
        net_flow = net * price if price else None
        flow_usd += net_flow or 0.0
        if amount > 0 or inn or out:
            positions.append({"symbol": t["symbol"], "mint": tok, "amount": amount, "price": price, "value": value, "net_flow": net_flow, "transfers": len(inn) + len(out)})
        time.sleep(0.05)
    holdings = sum(p["value"] or 0.0 for p in positions)
    return {"address": address, "chain": "robinhood", "holdings": holdings, "pnl": None, "net_flow_usd": flow_usd, "swaps": swaps,
            "positions": sorted(positions, key=lambda p: -(p["value"] or 0.0)),
            "note": f"Tracked winners only, last {blocks:,} blocks (~{blocks / 36000:.0f}h) of Transfer logs on the Robinhood Chain RPC; holdings at DexScreener prices."}


# ------------------------------------------------------------------ orchestration

def top_wallets(chain: str, top: dict, store=None, detail_budget_s: float = 240.0) -> dict:
    """Rank buyer wallets across the winners; when `store` is given, publish partial results as they arrive."""
    winners = top["items"][:6]
    t0 = time.time()
    wallets = sol_buyers(winners) if chain == "solana" else rh_buyers(winners)
    ranked = sorted(wallets.values(), key=lambda w: (-w["score"], -len(w["buys"])))[:8]
    note = ("Smart-money score = presence as a buyer across today's top gainers (weighted by gain). Sampled from the public RPC, not a full index."
            if chain == "solana" else
            "Score = presence as a swapper across today's top gainers on Robinhood Chain (Uniswap Swap logs, last ~11h). Holdings from Transfer logs + balances.")
    res = {"chain": chain, "items": ranked, "sampled_wallets": len(wallets), "updated": datetime.utcnow().isoformat() + "Z", "note": note, "details": "loading"}
    if store:
        store(f"chain:{chain}:wallets", res)
    _log(chain, "buyers ranked", len(ranked), "of", len(wallets), f"{time.time() - t0:.0f}s")
    price_of = {w["address"]: w["price"] for w in winners if w.get("price")}
    price_of[WSOL] = _sol_price() if chain == "solana" else 0.0
    t1 = time.time()
    for w in ranked:
        if time.time() - t1 > detail_budget_s:
            break
        try:
            det = sol_wallet(w["address"], price_of, sample=10) if chain == "solana" else rh_wallet(w["address"], winners)
            w["holdings"], w["pnl"], w["swaps"] = det.get("holdings"), det.get("pnl"), det.get("swaps")
            w["positions"] = det.get("positions", [])[:6]
            if store:
                store(f"chain:{chain}:wallets", {**res, "updated": datetime.utcnow().isoformat() + "Z"})
        except Exception as e:  # noqa: BLE001
            _log("wallet detail", chain, w["address"][:8], e)
    res["details"] = "done"
    res["updated"] = datetime.utcnow().isoformat() + "Z"
    return res


def refresh_coins(chain: str, store) -> dict:
    t0 = time.time()
    cands = candidate_tokens(chain)
    top = top_coins(chain, cands)
    store(f"chain:{chain}:top", top)
    store(f"chain:{chain}:new", new_coins(chain, cands))
    _log(chain, "coins done", f"{time.time() - t0:.0f}s", "candidates", len(cands), "top", len(top["items"]))
    return top


def refresh_wallets(chain: str, top: dict, store) -> None:
    t0 = time.time()
    try:
        store(f"chain:{chain}:wallets", top_wallets(chain, top, store))
        _log(chain, "wallets done", f"{time.time() - t0:.0f}s")
    except Exception as e:  # noqa: BLE001
        _log(chain, "wallets failed", e)


def start_refresher(store, load) -> threading.Thread:
    def loop():
        while True:
            tops = {}
            for chain in CHAINS:  # coins first for every chain (fast), wallets after (slow)
                try:
                    tops[chain] = refresh_coins(chain, store)
                except Exception as e:  # noqa: BLE001
                    _log(chain, "coins error", e)
            for chain, top in tops.items():
                refresh_wallets(chain, top, store)
            time.sleep(REFRESH_MIN * 60)
    th = threading.Thread(target=loop, name="chain-refresher", daemon=True)
    th.start()
    return th
