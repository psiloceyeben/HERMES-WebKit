#!/usr/bin/env python3
"""Daily options-chain capture for the HOODBarons archive.

Run by cron after the US close. Fetches the chain for every ticker in
watchlist.txt (one per line) through the same fetch_chain the API uses, which
writes strikes, OI, IV, and the computed levels into hoodbarons.db. This is how
we build our own options history instead of buying a feed.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hoodbarons_api as api  # noqa: E402

WATCH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchlist.txt")


def main() -> None:
    tickers = [ln.strip().upper() for ln in open(WATCH) if ln.strip() and not ln.startswith("#")]
    ok, bad = 0, []
    for t in tickers:
        try:
            # bypass the 15-minute cache so the daily row is fresh
            with api.db() as c:
                c.execute("DELETE FROM cache WHERE key LIKE ?", (f"chain:{t}:%",))
            ch = api.fetch_chain(t, 45)
            ok += 1
            print(f"{t} spot={ch['spot']:.2f} flip={ch['profile']['gamma_flip']} cw={ch['profile']['call_wall']} pw={ch['profile']['put_wall']}")
        except Exception as e:  # noqa: BLE001
            bad.append(f"{t}: {str(e)[:80]}")
        time.sleep(2)
    print(f"snapshot done ok={ok} bad={len(bad)} {bad}")


if __name__ == "__main__":
    main()
