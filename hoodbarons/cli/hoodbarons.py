#!/usr/bin/env python3
"""hoodbarons — command-line client for HOODBarons (prometheus7.com/HOODBarons).

Stdlib only. Point it at any HOODBarons API with --api (default: the public one).

  hoodbarons gex SPY [--days 30]                      dealer gamma profile: net GEX, flip, walls, top strikes
  hoodbarons option SPY 770 2026-10-02 [--put]        scenario fan for one contract
  hoodbarons backtest sma_cross SPY [--crypto] [--years 5] [--cash 10000] [--start ... --end ...] [-p fast=20 -p slow=50]
  hoodbarons strategies                               list built-in strategies + parameters
  hoodbarons crypto BTC [--years 2]                   price, 7d/30d/1y change, realized vol
  hoodbarons chain top [solana|robinhood]             most profitable coins (24h) on the chain
  hoodbarons chain new [solana|robinhood]             newest launches
  hoodbarons chain wallets [solana|robinhood]         most profitable / smart-money wallets
  hoodbarons wallet <address> [--chain solana]        one wallet: holdings + swap flow
  hoodbarons ask "what is gamma exposure"             the HOODBarons knowledge base, with references
  hoodbarons archive [SPY]                            our own options-history archive status
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request

DEFAULT_API = "https://prometheus7.com/HOODBarons/api"
G, R, Y, B, D, X = "\033[32m", "\033[31m", "\033[33m", "\033[1m", "\033[2m", "\033[0m"


def color_on() -> bool:
    return sys.stdout.isatty()


def c(code: str, s: str) -> str:
    return f"{code}{s}{X}" if color_on() else s


def signed(v: float, d: int = 1, suffix: str = "%") -> str:
    if v is None:
        return "—"
    s = f"{v:+.{d}f}{suffix}"
    return c(G if v >= 0 else R, s)


def money(v: float) -> str:
    if v is None:
        return "—"
    a = abs(v)
    s = f"${a / 1e9:.2f}B" if a >= 1e9 else f"${a / 1e6:.1f}M" if a >= 1e6 else f"${a / 1e3:.1f}k" if a >= 1e3 else f"${a:.2f}"
    return c(G if v >= 0 else R, ("+" if v >= 0 else "-") + s)


def usd(v: float) -> str:
    """Unsigned dollar amount for volume, liquidity, holdings."""
    if v is None:
        return "—"
    a = abs(v)
    return f"${a / 1e9:.2f}B" if a >= 1e9 else f"${a / 1e6:.1f}M" if a >= 1e6 else f"${a / 1e3:.1f}k" if a >= 1e3 else f"${a:.2f}"


def table(rows: list[list[str]], head: list[str]) -> None:
    def strip(s: str) -> str:
        import re
        return re.sub(r"\033\[[0-9;]*m", "", s)
    w = [max(len(strip(str(r[i]))) for r in [head] + rows) for i in range(len(head))]
    print(c(D, "  ".join(str(h).ljust(w[i]) for i, h in enumerate(head))))
    for r in rows:
        print("  ".join(str(v) + " " * (w[i] - len(strip(str(v)))) for i, v in enumerate(r)))


class Client:
    def __init__(self, api: str):
        self.api = api.rstrip("/")

    def get(self, path: str, **q):
        url = self.api + path + ("?" + urllib.parse.urlencode({k: v for k, v in q.items() if v is not None}) if q else "")
        req = urllib.request.Request(url, headers={"User-Agent": "hoodbarons-cli/1.0"})
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read())

    def post(self, path: str, body: dict):
        req = urllib.request.Request(self.api + path, data=json.dumps(body).encode(),
                                     headers={"User-Agent": "hoodbarons-cli/1.0", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            return json.loads(r.read())


def cmd_gex(cl: Client, a) -> None:
    j = cl.get("/chain", ticker=a.ticker, days=a.days)
    p = j["profile"]
    print(c(B, f"{j['ticker']}  spot {j['spot']:.2f}") + c(D, f"  window {j['window_days']}d  {len(j['expiries'])} expiries  as of {j['asof'][:16]}Z  (delayed)"))
    print(f"  net GEX / 1%  {money(p['net_gex'])}   regime {c(G if p['net_gex'] > 0 else R, 'long gamma · dampening' if p['net_gex'] > 0 else 'short gamma · amplifying')}")
    flip = p.get("gamma_flip")
    print(f"  gamma flip    {c(Y, f'{flip:.2f}') if flip else '—'}" + (f"  ({(flip / j['spot'] - 1) * 100:+.1f}%)" if flip else ""))
    print(f"  call wall     {c(G, str(p['call_wall']))}    put wall {c(R, str(p['put_wall']))}    ATM IV {j['atm_iv'] * 100:.1f}% ({j['atm_iv_expiry']})")
    top = sorted(p["strikes"], key=lambda s: -abs(s["net_gex"]))[:12]
    top.sort(key=lambda s: s["strike"])
    print()
    table([[f"{s['strike']:g}", f"{s['call_oi']:,}", f"{s['put_oi']:,}", money(s['call_gex']), money(s['put_gex']), money(s['net_gex'])] for s in top],
          ["strike", "call OI", "put OI", "call GEX", "put GEX", "net"])


def cmd_option(cl: Client, a) -> None:
    j = cl.get("/option", ticker=a.ticker, strike=a.strike, expiry=a.expiry, kind="put" if a.put else "call", iv=a.iv)
    g = j["greeks"]
    m = j.get("market")
    print(c(B, f"{j['ticker']} {j['strike']:g} {j['kind'].upper()} {j['expiry']}") + c(D, f"  spot {j['spot']:.2f}  {j['dte']:.1f} DTE  IV {j['iv'] * 100:.1f}%"))
    print(f"  model ${j['price']:.2f}" + (f"   market mid ${(m['bid'] + m['ask']) / 2:.2f}  (bid {m['bid']} ask {m['ask']} OI {m['oi']})" if m and (m["bid"] or m["ask"]) else "   (no market quote)"))
    print(f"  delta {g['delta']:+.3f}  gamma {g['gamma']:.4f}  theta {g['theta']:+.3f}/day  vega {g['vega']:.3f}   ±1σ 5d {j['expected_move_5d']:.2f}  ±1σ expiry {j['expected_move_expiry']:.2f}")
    lv = j.get("levels") or {}
    print(c(D, f"  levels: flip {lv.get('gamma_flip') and round(lv['gamma_flip'], 2)}  call wall {lv.get('call_wall')}  put wall {lv.get('put_wall')}   IV rule: {j['iv_rule']}"))
    print()
    hs = [p["h"] for p in j["grid"][0]["prices"]]
    rows = []
    for r in j["grid"]:
        rows.append([r["level"], f"{r['spot']:.2f}", signed(r["move_pct"])] + [f"${p['price']:.2f} " + signed(p["pnl_pct"], 0) for p in r["prices"]])
    table(rows, ["level", "spot", "move"] + hs)


def cmd_backtest(cl: Client, a) -> None:
    params = {}
    for kv in a.param or []:
        k, v = kv.split("=", 1)
        params[k] = float(v)
    j = cl.post("/backtest", {"symbol": a.symbol, "asset": "crypto" if a.crypto else "equity", "strategy": a.strategy,
                              "params": params, "start": a.start, "end": a.end, "cash": a.cash, "years": a.years})
    print(c(B, f"{j['name']} on {j['symbol']}") + c(D, f"  {j['start']} → {j['end']}  cash ${j['cash0']:,.0f}  params {j['params']}"))
    print(f"  total {signed(j['total_return'] * 100)}   buy&hold {signed(j['buy_hold_return'] * 100)}   CAGR {signed(j['cagr'] * 100)}   max DD {signed(j['max_drawdown'] * 100)}   Sharpe {j['sharpe']:.2f}   trades {j['trades']}")
    print(c(D, "  " + j["note"]))
    eq = j["equity"]
    if eq and not a.quiet:
        # ascii sparkline of the equity curve
        vals = [e["eq"] for e in eq]
        lo, hi = min(vals), max(vals)
        bars = "▁▂▃▄▅▆▇█"
        step = max(1, len(vals) // 60)
        print("  " + "".join(bars[min(7, int((v - lo) / (hi - lo + 1e-9) * 7.999))] for v in vals[::step]))
    if a.trades:
        print()
        table([[t["date"], t["side"], f"{t['px']:.2f}", ("credit $%.0f" % t["credit"]) if "credit" in t else ("P&L $%.0f" % t["pnl"]) if "pnl" in t else (f"{t['units']:.2f} units" if t.get("units") else "")]
               for t in j["trade_log"][-20:]], ["date", "action", "price", "detail"])


def cmd_strategies(cl: Client, a) -> None:
    j = cl.get("/strategies")["strategies"]
    for k, s in j.items():
        print(f"{c(B, k):28s} {s['name']}  {c(D, s['asset'])}  params {s['params']}\n    {c(D, s['desc'])}")


def cmd_crypto(cl: Client, a) -> None:
    j = cl.get("/history", symbol=a.symbol, asset="crypto", years=a.years)
    y = [b["close"] for b in j["bars"]]
    last = y[-1]
    ch = lambda n: (last / y[max(0, len(y) - 1 - n)] - 1) * 100  # noqa: E731
    import math
    rets = [math.log(y[i] / y[i - 1]) for i in range(len(y) - 30, len(y))]
    m = sum(rets) / len(rets)
    sd = math.sqrt(sum((x - m) ** 2 for x in rets) / (len(rets) - 1))
    print(c(B, f"{a.symbol.upper()}/USDT  ${last:,.4f}" if last < 1 else f"{a.symbol.upper()}/USDT  ${last:,.2f}") + c(D, f"  {j['bars'][0]['date']} → {j['bars'][-1]['date']}"))
    print(f"  7d {signed(ch(7))}   30d {signed(ch(30))}   1y {signed(ch(365))}   realized vol (30d) {sd * math.sqrt(365) * 100:.0f}%")


def _coin_rows(items: list[dict]) -> list[list[str]]:
    rows = []
    for t in items:
        rows.append([t.get("symbol", "?")[:12], f"${t['price']:.6g}" if t.get("price") else "—", signed(t.get("h1")), signed(t.get("h24")),
                     usd(t.get("volume24")), usd(t.get("liquidity")),
                     usd(t.get("mcap")) if t.get("mcap") else "—", t.get("age", "—"), (t.get("address") or "")[:14] + "…"])
    return rows


def cmd_chain(cl: Client, a) -> None:
    what, chain = a.what, a.chain
    if what == "top":
        j = cl.get("/chain/top", chain=chain)
        print(c(B, f"{chain} · most profitable coins, 24h") + c(D, f"  {j.get('note', '')}  updated {j.get('updated', '')}"))
        table(_coin_rows(j["items"]), ["symbol", "price", "1h", "24h", "vol 24h", "liquidity", "mcap", "age", "address"])
    elif what == "new":
        j = cl.get("/chain/new", chain=chain)
        print(c(B, f"{chain} · newest launches") + c(D, f"  {j.get('note', '')}  updated {j.get('updated', '')}"))
        table([[t.get("symbol", "?")[:12], (t.get("name") or "")[:22], t.get("age", "—"), usd(t.get("mcap")) if t.get("mcap") else "—",
                usd(t.get("liquidity")), t.get("source", ""), (t.get("address") or "")[:16] + "…"] for t in j["items"]],
              ["symbol", "name", "age", "mcap", "liquidity", "source", "address"])
    elif what == "wallets":
        j = cl.get("/chain/wallets", chain=chain)
        print(c(B, f"{chain} · most profitable wallets") + c(D, f"  {j.get('note', '')}  updated {j.get('updated', '')}"))
        table([[w["address"][:10] + "…" + w["address"][-4:], f"{w.get('score', 0):.1f}", str(w.get("winners", 0)), money(w.get("pnl")) if w.get("pnl") is not None else "—",
                usd(w.get("holdings")), str(w.get("swaps", "—")), ", ".join(w.get("tokens", [])[:4])] for w in j["items"]],
              ["wallet", "score", "winners", "flow PnL", "holdings", "swaps", "tokens"])


def cmd_wallet(cl: Client, a) -> None:
    j = cl.get("/chain/wallet", chain=a.chain, address=a.address)
    print(c(B, f"{a.chain} wallet {a.address}") + c(D, f"  {j.get('note', '')}"))
    print(f"  swaps seen {j.get('swaps')}   flow PnL {money(j.get('pnl')) if j.get('pnl') is not None else '—'}   holdings {usd(j.get('holdings'))}")
    if j.get("positions"):
        table([[p.get("symbol", "?")[:12], f"{p['amount']:.4g}", f"${p['price']:.6g}" if p.get("price") else "—", usd(p.get("value")), money(p.get("net_flow")) if p.get("net_flow") is not None else "—"] for p in j["positions"][:20]],
              ["token", "amount", "price", "value", "net flow"])


def cmd_ask(cl: Client, a) -> None:
    j = cl.post("/explain", {"question": " ".join(a.question), "context": {}})
    o = j["oracle"]
    if o.get("ok"):
        print(o.get("answer", "").strip())
        if o.get("references"):
            print(c(D, "refs: " + ", ".join(str(r if isinstance(r, str) else r.get("id") or r.get("title") or r) for r in o["references"])))
        print(c(D, f"[{o.get('status')} · {o.get('path')} · {o.get('latency_ms', 0):.0f} ms]"))
    else:
        print(c(R, "no answer: " + str(o.get("error"))))


def cmd_archive(cl: Client, a) -> None:
    j = cl.get("/archive", ticker=a.ticker)
    print(c(B, f"archive {j['ticker']}") + f"  rows {j['rows']}  {j['first']} → {j['last']}  tickers {', '.join(j['tickers'])}")
    if j["levels"]:
        table([[l["day"], f"{l['spot']:.2f}", money(l["net_gex"]), f"{l['gamma_flip']:.2f}" if l["gamma_flip"] else "—", str(l["call_wall"]), str(l["put_wall"])] for l in j["levels"][-15:]],
              ["day", "spot", "net GEX", "flip", "call wall", "put wall"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="hoodbarons", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--json", action="store_true", help="print raw JSON instead of tables")
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("gex"); p.add_argument("ticker"); p.add_argument("--days", type=int, default=30); p.set_defaults(fn=cmd_gex, raw=lambda cl, a: cl.get("/chain", ticker=a.ticker, days=a.days))
    p = sp.add_parser("option"); p.add_argument("ticker"); p.add_argument("strike", type=float); p.add_argument("expiry"); p.add_argument("--put", action="store_true"); p.add_argument("--iv", type=float)
    p.set_defaults(fn=cmd_option, raw=lambda cl, a: cl.get("/option", ticker=a.ticker, strike=a.strike, expiry=a.expiry, kind="put" if a.put else "call", iv=a.iv))
    p = sp.add_parser("backtest"); p.add_argument("strategy"); p.add_argument("symbol"); p.add_argument("--crypto", action="store_true"); p.add_argument("--years", type=int, default=5)
    p.add_argument("--cash", type=float, default=10000); p.add_argument("--start"); p.add_argument("--end"); p.add_argument("-p", "--param", action="append"); p.add_argument("--trades", action="store_true"); p.add_argument("--quiet", action="store_true")
    p.set_defaults(fn=cmd_backtest)
    sp.add_parser("strategies").set_defaults(fn=cmd_strategies, raw=lambda cl, a: cl.get("/strategies"))
    p = sp.add_parser("crypto"); p.add_argument("symbol"); p.add_argument("--years", type=int, default=2); p.set_defaults(fn=cmd_crypto)
    p = sp.add_parser("chain"); p.add_argument("what", choices=["top", "new", "wallets"]); p.add_argument("chain", nargs="?", default="solana", choices=["solana", "robinhood"]); p.set_defaults(fn=cmd_chain)
    p = sp.add_parser("wallet"); p.add_argument("address"); p.add_argument("--chain", default="solana", choices=["solana", "robinhood"]); p.set_defaults(fn=cmd_wallet)
    p = sp.add_parser("ask"); p.add_argument("question", nargs="+"); p.set_defaults(fn=cmd_ask)
    p = sp.add_parser("archive"); p.add_argument("ticker", nargs="?", default="SPY"); p.set_defaults(fn=cmd_archive)
    a = ap.parse_args(argv)
    cl = Client(a.api)
    try:
        if a.json and getattr(a, "raw", None):
            print(json.dumps(a.raw(cl, a), indent=1))
        else:
            a.fn(cl, a)
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error")
        except Exception:  # noqa: BLE001
            msg = str(e)
        print(c(R, f"error: {msg}"), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
