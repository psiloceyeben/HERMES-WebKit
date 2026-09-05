"""HOODBarons engine — options math, GEX profile, scenario pricing, backtester.

Pure functions over plain Python/numpy. No network. Shared by the API and the
snapshot daemon so the screener, the options check, and the backtester all use
ONE implementation of Black-Scholes and the dealer-gamma model.
"""
from __future__ import annotations

import math
from datetime import date, datetime

# ---------------------------------------------------------------- Black-Scholes

SQRT2 = math.sqrt(2.0)


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / SQRT2))


def _npdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_price(kind: str, s: float, k: float, t: float, iv: float, r: float = 0.045, q: float = 0.0) -> float:
    """European option price. kind 'call'|'put'. t in years."""
    if t <= 0 or iv <= 0:
        return max(0.0, (s - k) if kind == "call" else (k - s))
    d1 = (math.log(s / k) + (r - q + 0.5 * iv * iv) * t) / (iv * math.sqrt(t))
    d2 = d1 - iv * math.sqrt(t)
    if kind == "call":
        return s * math.exp(-q * t) * _ncdf(d1) - k * math.exp(-r * t) * _ncdf(d2)
    return k * math.exp(-r * t) * _ncdf(-d2) - s * math.exp(-q * t) * _ncdf(-d1)


def bs_greeks(kind: str, s: float, k: float, t: float, iv: float, r: float = 0.045, q: float = 0.0) -> dict:
    if t <= 0 or iv <= 0:
        itm = (s > k) if kind == "call" else (s < k)
        return {"delta": (1.0 if kind == "call" else -1.0) if itm else 0.0,
                "gamma": 0.0, "theta": 0.0, "vega": 0.0, "d1": 0.0, "d2": 0.0}
    sq = math.sqrt(t)
    d1 = (math.log(s / k) + (r - q + 0.5 * iv * iv) * t) / (iv * sq)
    d2 = d1 - iv * sq
    pdf = _npdf(d1)
    gamma = math.exp(-q * t) * pdf / (s * iv * sq)
    vega = s * math.exp(-q * t) * pdf * sq / 100.0  # per 1 vol point
    if kind == "call":
        delta = math.exp(-q * t) * _ncdf(d1)
        theta = (-(s * math.exp(-q * t) * pdf * iv) / (2 * sq)
                 - r * k * math.exp(-r * t) * _ncdf(d2)
                 + q * s * math.exp(-q * t) * _ncdf(d1)) / 365.0
    else:
        delta = -math.exp(-q * t) * _ncdf(-d1)
        theta = (-(s * math.exp(-q * t) * pdf * iv) / (2 * sq)
                 + r * k * math.exp(-r * t) * _ncdf(-d2)
                 - q * s * math.exp(-q * t) * _ncdf(-d1)) / 365.0
    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega, "d1": d1, "d2": d2}


def implied_vol(kind: str, price: float, s: float, k: float, t: float, r: float = 0.045, q: float = 0.0) -> float | None:
    """Bisection IV solve. Returns None if the price is outside no-arbitrage bounds."""
    if t <= 0 or price <= 0:
        return None
    lo, hi = 0.01, 5.0
    if bs_price(kind, s, k, t, lo, r, q) > price or bs_price(kind, s, k, t, hi, r, q) < price:
        return None
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if bs_price(kind, s, k, t, mid, r, q) > price:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def years_to(expiry: str, asof: datetime | None = None) -> float:
    """Fraction of a year from asof (default now, UTC) to 16:00 ET on expiry date."""
    asof = asof or datetime.utcnow()
    exp = datetime.strptime(expiry, "%Y-%m-%d").replace(hour=20)  # ~16:00 ET in UTC
    return max(0.0, (exp - asof).total_seconds() / (365.0 * 86400.0))


# ------------------------------------------------------------------- GEX model

def gex_profile(spot: float, rows: list[dict], r: float = 0.045) -> dict:
    """Dealer gamma exposure per strike.

    rows: [{strike, kind:'call'|'put', oi, iv, t}]  (t in years)
    Convention: dealers are long the calls customers sold and short the puts
    customers bought → call GEX positive, put GEX negative. Per-strike GEX in
    dollars per 1% move = gamma * OI * 100 * spot^2 * 0.01.
    Returns per-strike table, net GEX, gamma flip, call wall, put wall.
    """
    by_strike: dict[float, dict] = {}
    for row in rows:
        k, oi, iv, t = float(row["strike"]), float(row.get("oi") or 0), float(row.get("iv") or 0), float(row["t"])
        if oi <= 0 or iv <= 0 or t <= 0:
            continue
        g = bs_greeks(row["kind"], spot, k, t, iv, r)["gamma"]
        val = g * oi * 100.0 * spot * spot * 0.01
        cell = by_strike.setdefault(k, {"strike": k, "call_gex": 0.0, "put_gex": 0.0, "call_oi": 0, "put_oi": 0})
        if row["kind"] == "call":
            cell["call_gex"] += val
            cell["call_oi"] += int(oi)
        else:
            cell["put_gex"] -= val
            cell["put_oi"] += int(oi)
    table = sorted(by_strike.values(), key=lambda c: c["strike"])
    for c in table:
        c["net_gex"] = c["call_gex"] + c["put_gex"]
    net = sum(c["net_gex"] for c in table)
    call_wall = max(table, key=lambda c: c["call_gex"])["strike"] if table else None
    put_wall = min(table, key=lambda c: c["put_gex"])["strike"] if table else None
    flip = gamma_flip(spot, rows, r)
    return {"spot": spot, "net_gex": net, "gamma_flip": flip, "call_wall": call_wall,
            "put_wall": put_wall, "regime": ("positive" if net > 0 else "negative"),
            "strikes": table}


def gamma_flip(spot: float, rows: list[dict], r: float = 0.045) -> float | None:
    """Spot level where net dealer gamma crosses zero, scanning ±15% around spot."""
    def net_at(s: float) -> float:
        tot = 0.0
        for row in rows:
            oi, iv, t = float(row.get("oi") or 0), float(row.get("iv") or 0), float(row["t"])
            if oi <= 0 or iv <= 0 or t <= 0:
                continue
            g = bs_greeks(row["kind"], s, float(row["strike"]), t, iv, r)["gamma"]
            v = g * oi * 100.0 * s * s * 0.01
            tot += v if row["kind"] == "call" else -v
        return tot
    lo, hi = spot * 0.85, spot * 1.15
    n = 60
    prev_s, prev_v = lo, net_at(lo)
    for i in range(1, n + 1):
        s = lo + (hi - lo) * i / n
        v = net_at(s)
        if prev_v <= 0 < v or prev_v >= 0 > v:
            # linear interpolation for the crossing
            if v == prev_v:
                return s
            return prev_s + (s - prev_s) * (-prev_v) / (v - prev_v)
        prev_s, prev_v = s, v
    return None


def expected_move(spot: float, iv: float, days: float) -> float:
    return spot * iv * math.sqrt(max(days, 0.0) / 365.0)


# ---------------------------------------------------------- scenario pricing

def scenario_fan(kind: str, spot: float, strike: float, expiry: str, iv: float, levels: dict,
                 r: float = 0.045, asof: datetime | None = None) -> dict:
    """Price the option across spot levels × time horizons under a stated IV rule.

    IV rule (declared, not hidden): below the gamma flip dealers are short gamma,
    so moves expand and IV is marked +20% relative; above the flip and inside the
    walls moves are dampened and IV is marked −10% relative; at the current spot
    IV is unchanged. This is a regime heuristic, labeled as such in the UI.
    """
    t0 = years_to(expiry, asof)
    dte = t0 * 365.0
    base_price = bs_price(kind, spot, strike, t0, iv, r)
    greeks = bs_greeks(kind, spot, strike, t0, iv, r)
    flip = levels.get("gamma_flip")
    em1 = expected_move(spot, iv, 1)
    em5 = expected_move(spot, iv, 5)
    emx = expected_move(spot, iv, dte)
    spots = [
        ("put wall", levels.get("put_wall")),
        ("−1σ to expiry", spot - emx),
        ("−1σ 5d", spot - em5),
        ("gamma flip", flip),
        ("now", spot),
        ("+1σ 5d", spot + em5),
        ("+1σ to expiry", spot + emx),
        ("call wall", levels.get("call_wall")),
    ]
    spots = [(n, float(v)) for n, v in spots if v is not None and v > 0]
    spots.sort(key=lambda x: x[1])
    horizons = [("now", 0.0), ("+1d", 1.0), ("+5d", 5.0), ("expiry", dte)]
    horizons = [(n, min(d, dte)) for n, d in horizons]
    grid = []
    for name, s in spots:
        row = {"level": name, "spot": s, "move_pct": (s / spot - 1.0) * 100.0, "prices": []}
        if flip and s < flip:
            iv_s = iv * 1.20
        elif abs(s - spot) < 1e-9:
            iv_s = iv
        else:
            iv_s = iv * 0.90
        row["iv"] = iv_s
        for hname, days in horizons:
            t = max(0.0, t0 - days / 365.0)
            p = bs_price(kind, s, strike, t, iv_s, r)
            row["prices"].append({"h": hname, "price": p,
                                  "pnl_pct": ((p / base_price - 1.0) * 100.0) if base_price > 0 else None})
        grid.append(row)
    return {"kind": kind, "spot": spot, "strike": strike, "expiry": expiry, "dte": dte, "iv": iv,
            "price": base_price, "greeks": greeks, "expected_move_1d": em1,
            "expected_move_5d": em5, "expected_move_expiry": emx, "grid": grid,
            "iv_rule": "below flip ×1.20, above/at walls ×0.90, at spot ×1.00 (regime heuristic)"}


# ------------------------------------------------------------------ backtester

def _sma(xs: list[float], n: int, i: int) -> float | None:
    if i + 1 < n:
        return None
    return sum(xs[i + 1 - n:i + 1]) / n


def _rsi(closes: list[float], n: int, i: int) -> float | None:
    if i < n:
        return None
    gains = losses = 0.0
    for j in range(i - n + 1, i + 1):
        d = closes[j] - closes[j - 1]
        if d > 0:
            gains += d
        else:
            losses -= d
    if losses == 0:
        return 100.0
    rs = (gains / n) / (losses / n)
    return 100.0 - 100.0 / (1.0 + rs)


def _hv(closes: list[float], n: int, i: int) -> float | None:
    if i < n:
        return None
    rets = [math.log(closes[j] / closes[j - 1]) for j in range(i - n + 1, i + 1)]
    m = sum(rets) / n
    var = sum((x - m) ** 2 for x in rets) / max(n - 1, 1)
    return math.sqrt(var * 252.0)


STRATEGIES = {
    "buy_hold": {"name": "Buy & hold", "asset": "any", "params": {}, "desc": "Buy on day one, never sell. The benchmark every other strategy has to beat."},
    "dca": {"name": "Dollar-cost average", "asset": "any", "params": {"every_days": 7},
            "desc": "Invest an equal slice of the starting cash every N days regardless of price."},
    "sma_cross": {"name": "SMA crossover", "asset": "any", "params": {"fast": 20, "slow": 50},
                  "desc": "Long when the fast average is above the slow one, cash otherwise. Momentum."},
    "rsi_revert": {"name": "RSI mean reversion", "asset": "any", "params": {"n": 2, "buy_below": 10, "sell_above": 70},
                   "desc": "Buy when the short RSI is washed out, sell when it recovers. Mean reversion."},
    "covered_call": {"name": "Covered call", "asset": "equity", "params": {"delta": 0.30, "dte": 30},
                     "desc": "Hold shares, sell a ~30-delta call every month. Modeled option prices."},
    "wheel": {"name": "Wheel", "asset": "equity", "params": {"delta": 0.30, "dte": 30},
              "desc": "Sell cash-secured puts until assigned, then sell covered calls until called away. Modeled."},
    "condor_walls": {"name": "Iron condor at the walls", "asset": "equity", "params": {"dte": 30, "width_pct": 2.0, "sigma": 1.25},
                     "desc": "Short strangle at ±1.25σ of the 30-day expected move (wall proxy until the GEX archive matures), wings 2% out, 10% of equity at risk per trade. Modeled."},
}


def _strike_for_delta(kind: str, s: float, t: float, iv: float, target: float, r: float) -> float:
    """Find the strike whose |delta| is closest to target, on a 0.5% grid."""
    best, best_d = s, 9.0
    for i in range(-60, 61):
        k = s * (1.0 + i * 0.005)
        d = abs(bs_greeks(kind, s, k, t, iv, r)["delta"])
        if abs(d - target) < best_d:
            best, best_d = k, abs(d - target)
    return best


def run_backtest(strategy: str, bars: list[dict], params: dict | None = None, cash0: float = 10_000.0,
                 r: float = 0.045) -> dict:
    """bars: [{date:'YYYY-MM-DD', close: float}] ascending. Returns metrics + equity curve."""
    spec = STRATEGIES[strategy]
    p = dict(spec["params"])
    p.update({k: v for k, v in (params or {}).items() if k in p})
    closes = [float(b["close"]) for b in bars]
    dates = [b["date"] for b in bars]
    n = len(closes)
    if n < 30:
        raise ValueError("need at least 30 bars")
    cash, units = cash0, 0.0
    equity, trades, notes = [], [], []
    # option-leg state for the modeled strategies
    leg = None  # {kind, strike, expiry_idx, premium, contracts}
    dca_left = cash0
    for i in range(n):
        s = closes[i]
        if strategy == "buy_hold":
            if i == 0:
                units, cash = cash / s, 0.0
                trades.append({"date": dates[i], "side": "buy", "px": s, "units": units})
        elif strategy == "dca":
            if i % int(p["every_days"]) == 0 and dca_left > 0:
                slice_ = min(dca_left, cash0 * p["every_days"] / max(n, 1))
                slice_ = max(slice_, min(dca_left, cash0 / 52))
                units += slice_ / s
                cash -= slice_
                dca_left -= slice_
                trades.append({"date": dates[i], "side": "buy", "px": s, "units": slice_ / s})
        elif strategy == "sma_cross":
            f, sl = _sma(closes, int(p["fast"]), i), _sma(closes, int(p["slow"]), i)
            if f is not None and sl is not None:
                if f > sl and units == 0:
                    units, cash = cash / s, 0.0
                    trades.append({"date": dates[i], "side": "buy", "px": s, "units": units})
                elif f < sl and units > 0:
                    cash, units = units * s, 0.0
                    trades.append({"date": dates[i], "side": "sell", "px": s, "units": 0})
        elif strategy == "rsi_revert":
            rsi = _rsi(closes, int(p["n"]), i)
            if rsi is not None:
                if rsi < p["buy_below"] and units == 0:
                    units, cash = cash / s, 0.0
                    trades.append({"date": dates[i], "side": "buy", "px": s, "units": units})
                elif rsi > p["sell_above"] and units > 0:
                    cash, units = units * s, 0.0
                    trades.append({"date": dates[i], "side": "sell", "px": s, "units": 0})
        elif strategy in ("covered_call", "wheel", "condor_walls"):
            hv = _hv(closes, 20, i)
            iv = (hv * 1.10) if hv else None  # modeled IV = 1.1 × 20d realized
            dte = int(p["dte"])
            if leg and i >= leg["expiry_idx"]:
                # settle
                if strategy == "condor_walls":
                    pnl = leg["premium"]
                    for k_, kind_ in ((leg["short_put"], "put"), (leg["short_call"], "call")):
                        pnl -= max(0.0, (k_ - s) if kind_ == "put" else (s - k_))
                    for k_, kind_ in ((leg["long_put"], "put"), (leg["long_call"], "call")):
                        pnl += max(0.0, (k_ - s) if kind_ == "put" else (s - k_))
                    cash += pnl * leg["contracts"] * 100.0
                    trades.append({"date": dates[i], "side": "condor settle", "px": s, "pnl": pnl * leg["contracts"] * 100.0})
                elif leg["kind"] == "call":
                    if s > leg["strike"]:
                        cash += units * leg["strike"]
                        trades.append({"date": dates[i], "side": "called away", "px": leg["strike"], "units": 0})
                        units = 0.0
                elif leg["kind"] == "put":
                    if s < leg["strike"]:
                        units = leg["contracts"] * 100.0
                        cash -= units * leg["strike"]
                        trades.append({"date": dates[i], "side": "assigned", "px": leg["strike"], "units": units})
                leg = None
            if iv and leg is None and i + dte < n:
                t = dte / 365.0
                if strategy == "condor_walls":
                    em = expected_move(s, iv, dte) * float(p["sigma"])
                    sp, sc = s - em, s + em
                    w = s * float(p["width_pct"]) / 100.0
                    lp, lc = sp - w, sc + w
                    credit = (bs_price("put", s, sp, t, iv, r) + bs_price("call", s, sc, t, iv, r)
                              - bs_price("put", s, lp, t, iv, r) - bs_price("call", s, lc, t, iv, r))
                    max_loss = w - credit
                    contracts = max(1, int((cash * 0.10) / max(max_loss * 100.0, 1.0)))  # risk 10% of equity per condor
                    leg = {"kind": "condor", "short_put": sp, "short_call": sc, "long_put": lp, "long_call": lc,
                           "premium": credit, "contracts": contracts, "expiry_idx": i + dte}
                    trades.append({"date": dates[i], "side": "sell condor", "px": s, "credit": credit * contracts * 100.0,
                                   "short_put": sp, "short_call": sc})
                elif strategy == "covered_call" or (strategy == "wheel" and units > 0):
                    if units == 0 and strategy == "covered_call":
                        units = math.floor(cash / s / 100) * 100 or math.floor(cash / s)
                        cash -= units * s
                        trades.append({"date": dates[i], "side": "buy", "px": s, "units": units})
                    if units >= 100:
                        k = _strike_for_delta("call", s, t, iv, float(p["delta"]), r)
                        prem = bs_price("call", s, k, t, iv, r)
                        contracts = int(units // 100)
                        cash += prem * contracts * 100.0
                        leg = {"kind": "call", "strike": k, "premium": prem, "contracts": contracts, "expiry_idx": i + dte}
                        trades.append({"date": dates[i], "side": "sell call", "px": s, "strike": k, "credit": prem * contracts * 100.0})
                elif strategy == "wheel" and units == 0:
                    k = _strike_for_delta("put", s, t, iv, float(p["delta"]), r)
                    contracts = int(cash // (k * 100.0))
                    if contracts >= 1:
                        prem = bs_price("put", s, k, t, iv, r)
                        cash += prem * contracts * 100.0
                        leg = {"kind": "put", "strike": k, "premium": prem, "contracts": contracts, "expiry_idx": i + dte}
                        trades.append({"date": dates[i], "side": "sell put", "px": s, "strike": k, "credit": prem * contracts * 100.0})
        # mark to market (open option legs marked at intrinsic-free premium: conservative, premium already banked)
        eq = cash + units * s
        if leg and strategy == "condor_walls":
            # mark open condor at current model value
            rem_t = max(0.0, (leg["expiry_idx"] - i) / 365.0)
            hv_ = _hv(closes, 20, i) or 0.2
            iv_ = hv_ * 1.10
            val = (bs_price("put", s, leg["short_put"], rem_t, iv_, r) + bs_price("call", s, leg["short_call"], rem_t, iv_, r)
                   - bs_price("put", s, leg["long_put"], rem_t, iv_, r) - bs_price("call", s, leg["long_call"], rem_t, iv_, r))
            eq -= val * leg["contracts"] * 100.0
        elif leg and leg["kind"] in ("call", "put"):
            rem_t = max(0.0, (leg["expiry_idx"] - i) / 365.0)
            hv_ = _hv(closes, 20, i) or 0.2
            eq -= bs_price(leg["kind"], s, leg["strike"], rem_t, hv_ * 1.10, r) * leg["contracts"] * 100.0
        equity.append(eq)
    # metrics
    final = equity[-1]
    total_ret = final / cash0 - 1.0
    years = max((n - 1) / 252.0, 1e-9)
    cagr = (final / cash0) ** (1.0 / years) - 1.0 if final > 0 else -1.0
    peak, mdd = equity[0], 0.0
    for e in equity:
        peak = max(peak, e)
        mdd = min(mdd, e / peak - 1.0)
    rets = [(equity[j] / equity[j - 1] - 1.0) for j in range(1, n) if equity[j - 1] > 0]
    if rets:
        m = sum(rets) / len(rets)
        sd = math.sqrt(sum((x - m) ** 2 for x in rets) / max(len(rets) - 1, 1))
        sharpe = (m / sd) * math.sqrt(252.0) if sd > 0 else 0.0
    else:
        sharpe = 0.0
    bh = closes[-1] / closes[0] - 1.0
    wins = [tr for tr in trades if tr.get("pnl", 0) > 0 or tr.get("side") in ("sell", "called away")]
    return {"strategy": strategy, "name": spec["name"], "params": p, "cash0": cash0, "final": final,
            "total_return": total_ret, "cagr": cagr, "max_drawdown": mdd, "sharpe": sharpe,
            "buy_hold_return": bh, "trades": len(trades), "trade_log": trades[-40:],
            "equity": [{"date": dates[j], "eq": equity[j]} for j in range(0, n, max(1, n // 400))] + [{"date": dates[-1], "eq": final}],
            "modeled": strategy in ("covered_call", "wheel", "condor_walls"),
            "note": ("Option legs priced with Black-Scholes at 1.1× 20-day realized vol; settlement at expiry only."
                     if strategy in ("covered_call", "wheel", "condor_walls") else "Daily closes, no slippage, no fees.")}
