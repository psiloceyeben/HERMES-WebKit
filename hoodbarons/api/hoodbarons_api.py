#!/usr/bin/env python3
"""HOODBarons API — stdlib HTTP server on 127.0.0.1:$PORT (8035 on Box A) behind nginx at /HOODBarons/api/.

Endpoints (all prefixed /HOODBarons/api):
  GET  /health
  GET  /strategies
  GET  /chain?ticker=SPY&days=30          chain rows + GEX profile over expiries inside the window, thinned to 8 (delayed data)
  POST /gex/manual {spot, rows:[{strike,kind,oi,iv,dte}]}
  GET  /option?ticker=SPY&strike=560&expiry=2026-09-19&kind=call
  POST /option/manual {kind,spot,strike,expiry,iv,levels:{gamma_flip,call_wall,put_wall}}
  GET  /history?symbol=SPY&asset=equity|crypto&years=5
  POST /backtest {symbol,asset,strategy,params,start,end,cash}
  POST /explain {question, context}       deterministic brief + HOODBarons Oracle answer
  GET  /archive?ticker=SPY                 our own options-history archive status + level history
Every chain fetch is captured into the archive (sqlite), so the history builds itself.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine  # noqa: E402
import chain  # noqa: E402

PORT = int(os.environ.get("PORT", "8031"))
DB = os.environ.get("HOODBARONS_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "hoodbarons.db"))
ORACLE_URL = os.environ.get("ORACLE_URL", "https://wanderaround.io/hoodbarons-oracle/chat")
PREFIX = "/HOODBarons/api"
RISK_FREE = 0.045
CHAIN_TTL = 15 * 60
HIST_TTL = 6 * 3600
_lock = threading.Lock()


# ------------------------------------------------------------------ storage

def db() -> sqlite3.Connection:
    c = sqlite3.connect(DB, timeout=30)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, ts REAL, body TEXT)")
    c.execute("""CREATE TABLE IF NOT EXISTS snapshots(
        ticker TEXT, day TEXT, expiry TEXT, strike REAL, kind TEXT, oi INTEGER, iv REAL, bid REAL, ask REAL, spot REAL,
        PRIMARY KEY(ticker, day, expiry, strike, kind))""")
    c.execute("""CREATE TABLE IF NOT EXISTS levels(
        ticker TEXT, day TEXT, spot REAL, net_gex REAL, gamma_flip REAL, call_wall REAL, put_wall REAL,
        PRIMARY KEY(ticker, day))""")
    return c


def cache_get(key: str, ttl: float):
    with db() as c:
        row = c.execute("SELECT ts, body FROM cache WHERE key=?", (key,)).fetchone()
    if row and time.time() - row[0] < ttl:
        return json.loads(row[1])
    return None


def cache_put(key: str, obj) -> None:
    with db() as c:
        c.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?)", (key, time.time(), json.dumps(obj)))


# ------------------------------------------------------------------ market data

def _yf():
    import yfinance as yf  # imported lazily so the engine works without it
    return yf


def _num(v, default=0.0):
    try:
        f = float(v)
        return default if f != f else f  # NaN guard
    except (TypeError, ValueError):
        return default


def pick_expiries(all_exp: list[str], days: int, now: datetime, cap: int = 8) -> list[str]:
    """Expiries inside the window, thinned to at most `cap` (always keeps nearest and farthest)."""
    inside = [e for e in all_exp if 0 < engine.years_to(e, now) * 365.0 <= days + 0.5]
    if not inside:
        inside = list(all_exp)[:2]
    if len(inside) <= cap:
        return inside
    idx = sorted({round(i * (len(inside) - 1) / (cap - 1)) for i in range(cap)})
    return [inside[i] for i in idx]


def fetch_chain(ticker: str, days: int = 30) -> dict:
    ticker = ticker.upper().strip()
    days = max(1, min(int(days), 120))
    key = f"chain:{ticker}:d{days}"
    hit = cache_get(key, CHAIN_TTL)
    if hit:
        hit["cached"] = True
        return hit
    yf = _yf()
    t = yf.Ticker(ticker)
    spot = None
    try:
        spot = float(t.fast_info["last_price"])
    except Exception:
        h = t.history(period="5d")
        spot = float(h["Close"].iloc[-1])
    now = datetime.utcnow()
    all_exp = list(t.options)
    if not all_exp:
        raise ValueError(f"no listed options for {ticker}")
    expiries = pick_expiries(all_exp, days, now)
    rows = []
    for exp in expiries:
        ch = t.option_chain(exp)
        tt = engine.years_to(exp, now)
        for kind, frame in (("call", ch.calls), ("put", ch.puts)):
            for rec in frame.itertuples(index=False):
                d = rec._asdict()
                iv = _num(d.get("impliedVolatility"))
                if iv < 0.03 or iv > 4.0:  # yfinance placeholder IVs (1e-5, 0.0517 after hours) are not quotes
                    iv = 0.0
                rows.append({"strike": float(d["strike"]), "kind": kind, "expiry": exp, "t": tt,
                             "oi": int(_num(d.get("openInterest"))), "iv": iv,
                             "bid": _num(d.get("bid")), "ask": _num(d.get("ask")),
                             "last": _num(d.get("lastPrice")), "volume": int(_num(d.get("volume")))})
    prof = engine.gex_profile(spot, rows, RISK_FREE)
    # keep the table readable: strikes within ±12% of spot
    prof["strikes"] = [c for c in prof["strikes"] if abs(c["strike"] / spot - 1) <= 0.12]
    # ATM IV from the expiry nearest 30 DTE (the near-dated ones are noisy)
    ref_exp = min(expiries, key=lambda e: abs(engine.years_to(e, now) * 365.0 - 30.0))
    atm_rows = [r_ for r_ in rows if r_["expiry"] == ref_exp and r_["iv"] > 0] or [r_ for r_ in rows if r_["iv"] > 0]
    atm = min(atm_rows, key=lambda r_: abs(r_["strike"] - spot)) if atm_rows else {"iv": 0.2}
    out = {"ticker": ticker, "spot": spot, "expiries": expiries, "window_days": days, "asof": now.isoformat() + "Z",
           "delayed": True, "atm_iv": atm["iv"], "atm_iv_expiry": ref_exp, "profile": prof,
           "rows": [r_ for r_ in rows if abs(r_["strike"] / spot - 1) <= 0.12], "cached": False}
    cache_put(key, out)
    capture_snapshot(ticker, spot, rows, prof)
    return out


def capture_snapshot(ticker: str, spot: float, rows: list[dict], prof: dict) -> None:
    day = date.today().isoformat()
    with db() as c:
        c.executemany("INSERT OR REPLACE INTO snapshots VALUES(?,?,?,?,?,?,?,?,?,?)",
                      [(ticker, day, r_["expiry"], r_["strike"], r_["kind"], r_["oi"], r_["iv"], r_["bid"], r_["ask"], spot)
                       for r_ in rows])
        c.execute("INSERT OR REPLACE INTO levels VALUES(?,?,?,?,?,?,?)",
                  (ticker, day, spot, prof["net_gex"], prof["gamma_flip"], prof["call_wall"], prof["put_wall"]))


def fetch_history(symbol: str, asset: str, years: int = 5) -> list[dict]:
    symbol = symbol.upper().strip()
    key = f"hist:{asset}:{symbol}:{years}"
    hit = cache_get(key, HIST_TTL)
    if hit:
        return hit
    bars: list[dict] = []
    if asset == "crypto":
        pair = symbol if symbol.endswith("USDT") else symbol.replace("-USD", "").replace("USD", "") + "USDT"
        end = int(time.time() * 1000)
        limit_days = years * 365
        while limit_days > 0:
            url = f"https://api.binance.com/api/v3/klines?symbol={pair}&interval=1d&limit=1000&endTime={end}"
            req = urllib.request.Request(url, headers={"User-Agent": "hoodbarons/1.0"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                kl = json.loads(resp.read())
            if not kl:
                break
            chunk = [{"date": datetime.utcfromtimestamp(k[0] / 1000).strftime("%Y-%m-%d"), "close": float(k[4])} for k in kl]
            bars = chunk + bars
            limit_days -= len(kl)
            end = kl[0][0] - 1
            if len(kl) < 1000:
                break
        bars = bars[-(years * 365):]
    else:
        yf = _yf()
        h = yf.Ticker(symbol).history(period=f"{years}y", auto_adjust=True)
        bars = [{"date": idx.strftime("%Y-%m-%d"), "close": float(row["Close"])} for idx, row in h.iterrows()]
    if not bars:
        raise ValueError(f"no history for {symbol}")
    cache_put(key, bars)
    return bars


# ------------------------------------------------------------------ explain layer

def _fmt_money(v: float) -> str:
    a = abs(v)
    if a >= 1e9:
        s = f"${a / 1e9:.2f}B"
    elif a >= 1e6:
        s = f"${a / 1e6:.1f}M"
    else:
        s = f"${a:,.0f}"
    return ("+" if v >= 0 else "−") + s


def brief_from_context(ctx: dict) -> list[str]:
    """Deterministic warrant chain built from the numbers on screen. No model, no guessing."""
    out: list[str] = []
    prof = ctx.get("profile")
    if prof:
        spot, net = prof["spot"], prof["net_gex"]
        sign = "long" if net > 0 else "short"
        out.append(f"Net dealer gamma is {_fmt_money(net)} per 1% move, so dealers are {sign} gamma.")
        if net > 0:
            out.append("Long-gamma dealers sell into rallies and buy dips to stay hedged, which dampens moves and favors pinning.")
        else:
            out.append("Short-gamma dealers must buy strength and sell weakness, which amplifies moves and widens ranges.")
        if prof.get("gamma_flip"):
            gf = prof["gamma_flip"]
            out.append(f"The gamma flip sits at {gf:,.2f} ({(gf / spot - 1) * 100:+.1f}% from spot); crossing it changes the regime from {'dampening to amplifying' if spot > gf else 'amplifying to dampening'}.")
        if prof.get("call_wall"):
            out.append(f"The call wall at {prof['call_wall']:,.0f} carries the largest positive gamma and acts as the natural ceiling while it holds.")
        if prof.get("put_wall"):
            out.append(f"The put wall at {prof['put_wall']:,.0f} carries the largest negative gamma and marks where hedging support concentrates.")
    fan = ctx.get("fan")
    if fan:
        g = fan["greeks"]
        out.append(f"Your {fan['strike']:g} {fan['kind']} expiring {fan['expiry']} ({fan['dte']:.0f} DTE) models at ${fan['price']:.2f} with IV {fan['iv'] * 100:.1f}%, delta {g['delta']:+.2f}, gamma {g['gamma']:.4f}, theta {g['theta']:+.3f}/day, vega {g['vega']:.3f}.")
        out.append(f"The 1σ expected move is ±{fan['expected_move_5d']:.2f} over 5 days and ±{fan['expected_move_expiry']:.2f} to expiry, which brackets the realistic price fan.")
        best = max((p for row in fan["grid"] for p in row["prices"] if p["pnl_pct"] is not None), key=lambda p: p["pnl_pct"], default=None)
        if best:
            lvl = next(row for row in fan["grid"] if best in row["prices"])
            out.append(f"The best cell in the fan is spot at the {lvl['level']} ({lvl['spot']:,.2f}) by {best['h']}: ${best['price']:.2f} ({best['pnl_pct']:+.0f}%).")
        out.append(f"IV rule applied: {fan['iv_rule']}; time decay is charged at each horizon.")
    bt = ctx.get("backtest")
    if bt:
        out.append(f"{bt['name']} on {ctx.get('symbol', '?')}: {bt['total_return'] * 100:+.1f}% total, CAGR {bt['cagr'] * 100:+.1f}%, max drawdown {bt['max_drawdown'] * 100:.1f}%, Sharpe {bt['sharpe']:.2f}, versus buy-and-hold {bt['buy_hold_return'] * 100:+.1f}%.")
        if bt.get("modeled"):
            out.append("Option legs are modeled with Black-Scholes at 1.1× realized volatility, so credits are an estimate until the snapshot archive supplies real quotes.")
    if not out:
        out.append("No setup loaded yet. Run a screen, an option check, or a backtest and the brief fills in from those numbers.")
    return out


def ask_oracle(question: str) -> dict:
    body = json.dumps({"text": question}).encode()  # the harness reads "text"
    req = urllib.request.Request(ORACLE_URL, data=body, headers={"Content-Type": "application/json",
                                                                  "User-Agent": "hoodbarons/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            j = json.loads(resp.read())
        return {"ok": True, "answer": j.get("response", ""), "references": j.get("references", []),
                "status": j.get("status"), "path": j.get("path"), "latency_ms": j.get("latency_ms")}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "answer": "", "error": str(e)[:200]}


# ------------------------------------------------------------------ http

class H(BaseHTTPRequestHandler):
    server_version = "hoodbarons/1.0"

    def log_message(self, fmt, *args):  # quiet
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, obj) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _route(self, method: str):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        if path.startswith(PREFIX):
            path = path[len(PREFIX):]
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        body = {}
        if method == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            if n > 200_000:
                return self._send(413, {"error": "body too large"})
            body = json.loads(self.rfile.read(n) or b"{}")
        try:
            if path == "/health":
                return self._send(200, {"ok": True, "time": datetime.utcnow().isoformat() + "Z"})
            if path == "/strategies":
                return self._send(200, {"strategies": engine.STRATEGIES})
            if path == "/chain" and method == "GET":
                return self._send(200, fetch_chain(q.get("ticker", "SPY"), int(q.get("days", 30))))
            if path == "/gex/manual" and method == "POST":
                spot = float(body["spot"])
                rows = [{"strike": float(r_["strike"]), "kind": r_["kind"], "oi": float(r_.get("oi") or 0),
                         "iv": float(r_.get("iv") or 0), "t": max(float(r_.get("dte") or 1), 0.1) / 365.0}
                        for r_ in body.get("rows", [])]
                prof = engine.gex_profile(spot, rows, RISK_FREE)
                return self._send(200, {"spot": spot, "profile": prof, "manual": True})
            if path == "/option" and method == "GET":
                ticker, kind = q.get("ticker", "SPY"), q.get("kind", "call")
                strike, expiry = float(q["strike"]), q["expiry"]
                dte_req = int(max(1.0, engine.years_to(expiry) * 365.0)) + 1
                ch = fetch_chain(ticker, max(30, dte_req))
                spot = ch["spot"]
                mkt = next((r_ for r_ in ch["rows"] if r_["kind"] == kind and abs(r_["strike"] - strike) < 1e-6 and r_["expiry"] == expiry), None)
                if mkt is None and expiry not in ch["expiries"]:
                    # exact expiry not in the thinned window: pull it directly for the quote
                    try:
                        t_ = _yf().Ticker(ticker)
                        oc = t_.option_chain(expiry)
                        fr = oc.calls if kind == "call" else oc.puts
                        hit_ = fr[abs(fr["strike"] - strike) < 1e-6]
                        if len(hit_):
                            d = hit_.iloc[0]
                            mkt = {"strike": strike, "kind": kind, "expiry": expiry, "oi": int(_num(d.get("openInterest"))),
                                   "iv": _num(d.get("impliedVolatility")), "bid": _num(d.get("bid")), "ask": _num(d.get("ask")),
                                   "last": _num(d.get("lastPrice")), "volume": int(_num(d.get("volume")))}
                    except Exception:  # noqa: BLE001
                        mkt = None
                iv = float(q.get("iv") or 0) or (mkt["iv"] if mkt and 0.03 < mkt["iv"] < 4 else ch["atm_iv"])
                fan = engine.scenario_fan(kind, spot, strike, expiry, iv, ch["profile"], RISK_FREE)
                fan["market"] = mkt
                fan["ticker"] = ticker
                fan["levels"] = {k: ch["profile"].get(k) for k in ("gamma_flip", "call_wall", "put_wall")}
                return self._send(200, fan)
            if path == "/option/manual" and method == "POST":
                fan = engine.scenario_fan(body.get("kind", "call"), float(body["spot"]), float(body["strike"]),
                                          body["expiry"], float(body["iv"]), body.get("levels", {}), RISK_FREE)
                fan["manual"] = True
                return self._send(200, fan)
            if path == "/history" and method == "GET":
                bars = fetch_history(q.get("symbol", "SPY"), q.get("asset", "equity"), int(q.get("years", 5)))
                return self._send(200, {"symbol": q.get("symbol", "SPY"), "bars": bars})
            if path == "/backtest" and method == "POST":
                bars = fetch_history(body.get("symbol", "SPY"), body.get("asset", "equity"), int(body.get("years", 5)))
                start, end = body.get("start"), body.get("end")
                if start:
                    bars = [b for b in bars if b["date"] >= start]
                if end:
                    bars = [b for b in bars if b["date"] <= end]
                res = engine.run_backtest(body.get("strategy", "buy_hold"), bars, body.get("params") or {},
                                          float(body.get("cash") or 10_000))
                res["symbol"] = body.get("symbol", "SPY")
                res["start"], res["end"] = bars[0]["date"], bars[-1]["date"]
                return self._send(200, res)
            if path == "/explain" and method == "POST":
                ctx = body.get("context") or {}
                question = (body.get("question") or "").strip()[:300]
                brief = brief_from_context(ctx)
                oracle = ask_oracle(question) if question else {"ok": False, "answer": "", "error": "no question"}
                return self._send(200, {"brief": brief, "oracle": oracle, "question": question})
            if path in ("/chain/top", "/chain/new", "/chain/wallets") and method == "GET":
                chain_ = q.get("chain", "solana")
                if chain_ not in chain.CHAINS:
                    return self._send(400, {"error": "chain must be solana or robinhood"})
                kind = path.rsplit("/", 1)[1]
                if kind != "wallets":
                    # live: DexScreener is near-real-time, so coins/launches are computed on request behind a 20 s cache
                    res = None if q.get("refresh") == "1" else cache_get(f"chain:{chain_}:{kind}", 20)
                    if res is None:
                        with _lock:
                            res = None if q.get("refresh") == "1" else cache_get(f"chain:{chain_}:{kind}", 20)
                            if res is None:
                                cands = chain.candidate_tokens(chain_)
                                res = chain.top_coins(chain_, cands) if kind == "top" else chain.new_coins(chain_, cands)
                                res["live"] = True
                                cache_put(f"chain:{chain_}:{kind}", res)
                    return self._send(200, res)
                res = cache_get(f"chain:{chain_}:{kind}", 10 * 24 * 3600)
                if res is None:
                    return self._send(200, {"chain": chain_, "items": [], "updated": None, "warming": True,
                                            "note": "First refresh is still running (wallet sampling takes a few minutes on the public RPC). Try again shortly."})
                return self._send(200, res)
            if path == "/chain/wallet" and method == "GET":
                chain_, address = q.get("chain", "solana"), (q.get("address") or "").strip()
                if not address:
                    return self._send(400, {"error": "address required"})
                key = f"wallet:{chain_}:{address.lower()}"
                res = cache_get(key, 10 * 60)
                if res is None:
                    if chain_ == "solana":
                        res = chain.sol_wallet(address, sample=25)
                    else:
                        top = cache_get(f"chain:robinhood:top", 10 * 24 * 3600) or {"items": []}
                        res = chain.rh_wallet(address, top["items"][:12])
                    cache_put(key, res)
                return self._send(200, res)
            if path == "/archive" and method == "GET":
                ticker = q.get("ticker", "SPY").upper()
                with db() as c:
                    n, first, last = c.execute("SELECT COUNT(*), MIN(day), MAX(day) FROM snapshots WHERE ticker=?", (ticker,)).fetchone()
                    lv = c.execute("SELECT day, spot, net_gex, gamma_flip, call_wall, put_wall FROM levels WHERE ticker=? ORDER BY day", (ticker,)).fetchall()
                    tickers = [r_[0] for r_ in c.execute("SELECT DISTINCT ticker FROM levels ORDER BY ticker")]
                return self._send(200, {"ticker": ticker, "rows": n, "first": first, "last": last, "tickers": tickers,
                                        "levels": [dict(zip(("day", "spot", "net_gex", "gamma_flip", "call_wall", "put_wall"), r_)) for r_ in lv]})
            return self._send(404, {"error": "not found"})
        except KeyError as e:
            return self._send(400, {"error": f"missing {e}"})
        except ValueError as e:
            return self._send(400, {"error": str(e)[:300]})
        except Exception as e:  # noqa: BLE001
            return self._send(500, {"error": f"{type(e).__name__}: {str(e)[:300]}"})

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")


def main() -> None:
    db().close()
    if os.environ.get("CHAIN_REFRESH", "1") == "1":
        chain.start_refresher(cache_put, cache_get)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    sys.stderr.write(f"hoodbarons api on 127.0.0.1:{PORT} db={DB} oracle={ORACLE_URL}\n")
    srv.serve_forever()


if __name__ == "__main__":
    main()
