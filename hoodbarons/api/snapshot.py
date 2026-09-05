#!/usr/bin/env python3
"""Daily options-chain capture for the HOODBarons archive.

Run by cron after the US close. Two lists:
  * watchlist.txt        — the DEEP list: full per-strike rows in `snapshots` + levels + gzip blob
  * watchlist_sp500.txt  — every S&P 500 constituent: levels row + gzip blob of live strikes
    (refreshed weekly from the public datasets/s-and-p-500-companies constituents file)
Resume-safe (skips tickers already captured today), throttled, time-budgeted, and it prunes the
API's chain cache so a 500-ticker run does not bloat the sqlite file.

  python3 snapshot.py                 full run
  LIMIT=10 python3 snapshot.py        first 10 tickers (smoke test)
  python3 snapshot.py --refresh-list  only refresh watchlist_sp500.txt
"""
import os
import sys
import time
import urllib.request
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hoodbarons_api as api  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
WATCH = os.path.join(HERE, "watchlist.txt")
SP500 = os.path.join(HERE, "watchlist_sp500.txt")
SP500_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
BUDGET_S = float(os.environ.get("SNAPSHOT_BUDGET_S", 3 * 3600))
PAUSE_S = float(os.environ.get("SNAPSHOT_PAUSE_S", 1.0))


def read_list(path: str) -> list[str]:
    if not os.path.exists(path):
        return []
    return [ln.strip().upper() for ln in open(path) if ln.strip() and not ln.startswith("#")]


def refresh_sp500(max_age_days: float = 7.0) -> int:
    """Download the constituents list if the local copy is missing or older than a week."""
    if os.path.exists(SP500) and (time.time() - os.path.getmtime(SP500)) < max_age_days * 86400:
        return len(read_list(SP500))
    req = urllib.request.Request(SP500_URL, headers={"User-Agent": "hoodbarons/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        text = r.read().decode("utf-8", "replace")
    lines = text.splitlines()
    head = [h.strip().lower() for h in lines[0].split(",")]
    col = head.index("symbol") if "symbol" in head else 0
    syms = []
    for ln in lines[1:]:
        parts = ln.split(",")
        if len(parts) > col and parts[col].strip():
            syms.append(parts[col].strip().upper().replace(".", "-"))  # BRK.B -> BRK-B for Yahoo
    if len(syms) < 400:
        raise ValueError(f"constituents file looks wrong ({len(syms)} symbols)")
    with open(SP500, "w") as f:
        f.write(f"# S&P 500 constituents, refreshed {date.today().isoformat()} from {SP500_URL}\n")
        f.write("\n".join(syms) + "\n")
    return len(syms)


def main() -> None:
    n_sp = refresh_sp500()
    if "--refresh-list" in sys.argv:
        print(f"sp500 list: {n_sp} symbols")
        return
    deep = read_list(WATCH)
    everything = deep + [t for t in read_list(SP500) if t not in deep]
    limit = int(os.environ.get("LIMIT", "0") or 0)
    if limit:
        everything = everything[:limit]
    today = date.today().isoformat()
    with api.db() as c:
        c.execute("DELETE FROM cache WHERE ts < ?", (time.time() - 86400,))  # prune stale chain/history cache
        done = {r[0] for r in c.execute("SELECT ticker FROM levels WHERE day=?", (today,))}
    todo = [t for t in everything if t not in done] if os.environ.get("FORCE") != "1" else everything
    print(f"snapshot {today}: {len(everything)} tickers ({len(deep)} deep), {len(done)} already captured, {len(todo)} to do")
    t0, ok, bad = time.time(), 0, []
    for i, t in enumerate(todo):
        if time.time() - t0 > BUDGET_S:
            print(f"budget reached after {i} tickers")
            break
        try:
            ch = api.fetch_chain(t, 45, cache=False, deep=(t in deep))
            ok += 1
            p = ch["profile"]
            print(f"{t:6s} spot={ch['spot']:.2f} net={p['net_gex'] / 1e6:+.0f}M flip={p['gamma_flip'] and round(p['gamma_flip'], 2)} cw={p['call_wall']} pw={p['put_wall']} exp={len(ch['expiries'])}", flush=True)
        except Exception as e:  # noqa: BLE001
            bad.append(f"{t}: {str(e)[:80]}")
            print(f"{t:6s} FAIL {str(e)[:100]}", flush=True)
        time.sleep(PAUSE_S)
    with api.db() as c:
        n_lv = c.execute("SELECT COUNT(*) FROM levels WHERE day=?", (today,)).fetchone()[0]
        size = os.path.getsize(api.DB) / 1e6
    print(f"snapshot done ok={ok} bad={len(bad)} in {time.time() - t0:.0f}s; levels today={n_lv}; db={size:.0f}MB")
    if bad:
        print("failures:", "; ".join(bad[:25]))


if __name__ == "__main__":
    main()
