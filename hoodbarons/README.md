# HOODBarons

Robinhood-styled finance strategy lab at **https://prometheus7.com/HOODBarons/**.
GEX screener, option scenario check, backtester on real daily history (stocks + crypto),
and a native Oracle7 explain layer running on its own instance compiled from a finance corpus.
Education and backtesting only: no orders, no accounts, no advice.

## Layout

| Path | What |
|---|---|
| `web/index.html` | The whole app. Single file, no build step, canvas charts, bottom-tab nav, Oracle drawer. |
| `api/engine.py` | Black-Scholes, greeks, IV solve, GEX profile (flip, call wall, put wall), scenario fan, backtester + 7 strategies. Pure Python. |
| `api/hoodbarons_api.py` | stdlib HTTP server. Chains via yfinance (delayed), crypto via Binance klines, sqlite cache + **options archive** (every screened chain is captured). `/explain` = deterministic brief from the on-screen numbers + Oracle answer. |
| `api/snapshot.py` + `watchlist.txt` + `watchlist_sp500.txt` | Daily cron capture into the archive (our own options history instead of a feed). Deep list (15 tickers) gets full per-strike rows; **all S&P 500 constituents** (list self-refreshes weekly from the public `datasets/s-and-p-500-companies` file, dots → dashes for Yahoo) get a levels row + a gzip blob of live strikes (±15% of spot, OI > 0, ~10 KB each ≈ 5 MB/day for the index). Resume-safe, 1 s pause, 3 h budget, ~3 s/ticker (~30 min for the index). `LIMIT=n` for a smoke run, `--refresh-list` to only refresh the list. |
| `api/chain.py` | On-chain radar for **Solana** and **Robinhood Chain** (chain id 4663): most profitable coins (24h gain × log volume), newest launches (pump.fun + newest DEX pairs), smart-money wallets (buyers across the day's winners, sampled from the public RPCs), single-wallet lookup. Background refresher every 20 min. |
| `cli/hoodbarons.py` | Stdlib CLI over the public API. `curl -o hoodbarons https://prometheus7.com/HOODBarons/cli/hoodbarons && python3 hoodbarons gex SPY`. Commands: gex, option, backtest, strategies, crypto, chain top/new/wallets, wallet, ask, archive. `--json` for raw output. |
| `corpus/hoodbarons_d1.jsonl` | 36 finance concept articles, 224 sentences, genus-form leads, collision-free aliases. Compiled into the Oracle builds. |
| `deploy/boxa_install.sh` | Box A: site + API service + nginx + cron + smoke. Idempotent. |
| `deploy/boxc_oracle_install.sh` | Box C: compile corpus into its own build pair, start the harness on :8098, nginx route, probes. |

## Live deploy map (2026-09-05)

**Box A (95.217.3.65, prometheus7.com)**
- Static: `/root/hermes/vessels/prometheus7/static/HOODBarons/index.html`
- API: `/root/hermes/vessels/prometheus7/hoodbarons/` (engine, api, snapshot, watchlist, `hoodbarons.db`), service `p7-hoodbarons.service`, **port 8035** (8031 was taken).
- nginx: `/etc/nginx/sites-enabled/hermes` → `^~ /HOODBarons/api/` → 127.0.0.1:8035, zone `hoodb` 120r/m (in nginx.conf). Backup `/root/hermes-vhost.backup.hoodbarons.*`.
- Cron: `35 21 * * 1-5 python3 .../snapshot.py >> snapshot.log` (after US close).

**Box C (89.167.7.54) — the HOODBarons Oracle7 instance**
- Builds (own copies, m13e7 bases untouched): `/opt/oracle-clm/fable-content/oracle-m-series-2026-08-15/hoodbarons_dbpedia_openstax_fable_bench_a_v1` and `hoodbarons_wiki_openstax_fable_build_a_v1` (33 new pages, 3 merged, 224 admitted, 0 rejected, each).
- Service `oracle-harness-hoodbarons.service`, **port 8098**, state `/opt/oracle-clm/harness_hoodbarons_state`, same `oracle_space_app_v21_autocorrect.py` as staging.
- nginx: `/etc/nginx/sites-enabled/wander` → `location = /hoodbarons-oracle/chat` → 127.0.0.1:8098/chat. Public: `https://wanderaround.io/hoodbarons-oracle/chat`.
- Chat request body is `{"text": "..."}` (NOT `message`; `message` yields `in_surface: ""` and a clarify response).
- Compiler/finalizer copies used: `/tmp/compile_fable_corpus_v3_hb.py` (fb_n starts after existing `fb*` page ids, the m13e7 bases already hold fb1..fb15) and `/tmp/finalize_fable_build_hb.py` (OUT/BASE hardcoded to the hoodbarons wiki build). The finalizer's deep validate reports `wikipedia_index_count_changed` but writes a `compiled` manifest; the runtime's non-deep validate accepts it and the fable pages resolve.

## On-chain sources (all keyless)
- DexScreener public API: token profiles, boosts, search, `tokens/v1/{chain}/{addrs}`; `chainId` values `solana` and `robinhood`.
- pump.fun `frontend-api-v3.pump.fun/coins?sort=created_timestamp` for the newest Solana mints.
- Solana public RPC `api.mainnet-beta.solana.com`: pool signatures → `getTransaction` (jsonParsed) → fee payer + token deltas; `getTokenAccountsByOwner` for holdings.
- Robinhood Chain RPC `rpc.mainnet.chain.robinhood.com` (chain 4663, ~10 blocks/s): `eth_getLogs` Uniswap Swap topics on winner pairs → `tx.from`; Transfer logs + `balanceOf` for holdings. Blockscout (`robinhoodchain.blockscout.com`) is Cloudflare-gated to scripts, so it is not used.
- Wallet "PnL" is a sampled flow proxy and labeled so in every response.

## Token
The site carries a `$HOODB` token card (Chain tab) and a footer line. Both read one constant near the top of the script in `web/index.html`:
```
const TOKEN = { symbol: '$HOODB', chain: 'solana', address: '', url: '' };
```
Set `address` when the token launches; the card flips from "launching soon" to "live on <chain>", shows the address, enables the Copy CA button, and the footer updates. Until then it says TBA with a scam warning.

## Mascot
`web/mascot.png` (420 px, served in the header and the Chain hero) and `web/mascot-full.png` (1254 px original). Both slots hide themselves if the file is missing.

## Behaviors worth knowing
- Chain window: `/chain?ticker=SPY&days=30` picks expiries inside the window, thinned to 8. yfinance placeholder IVs (<3% or >400%) are dropped; ATM IV comes from the expiry nearest 30 DTE.
- Scenario fan IV rule (declared in the UI): below the flip ×1.20, at/above the walls ×0.90, at spot ×1.00.
- Modeled strategies (covered call, wheel, condor) price legs with Black-Scholes at 1.1× 20-day realized vol; condor risks 10% of equity per trade. Wheel needs cash for 100 shares or it does nothing.
- Oracle answers definitional questions with a `[1]` reference into the corpus; "why" questions it cannot warrant are withheld by the head's law, which the UI shows as-is.

## Redeploy
```
tar czf /tmp/hb.tgz hoodbarons && scp /tmp/hb.tgz root@95.217.3.65:/root/hb.tgz
ssh root@95.217.3.65 'rm -rf /root/hoodbarons_upload && mkdir -p /root/hoodbarons_upload && tar xzf /root/hb.tgz -C /root/hoodbarons_upload --strip-components=1 && sed -i s/8031/8035/g /root/hoodbarons_upload/deploy/boxa_install.sh && bash /root/hoodbarons_upload/deploy/boxa_install.sh'
```
Corpus additions: append records to `corpus/hoodbarons_d1.jsonl` (or a `_d2.jsonl`), re-run the Box C script pointed at the patched compiler/finalizer copies, restart `oracle-harness-hoodbarons`.
