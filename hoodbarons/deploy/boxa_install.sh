#!/usr/bin/env bash
# HOODBarons — Box A install. Run as root on Box A from the uploaded bundle dir.
# Installs: static site at /HOODBarons/, API service on 127.0.0.1:8031, nginx locations, daily snapshot cron.
set -euo pipefail
SRC="${1:-/root/hoodbarons_upload}"
DST=/root/hermes/vessels/prometheus7/hoodbarons
STATIC=/root/hermes/vessels/prometheus7/static/HOODBarons
VHOST=/etc/nginx/sites-enabled/hermes

echo "== deps"
python3 -c "import yfinance" 2>/dev/null || pip3 install -q yfinance >/dev/null
python3 -c "import yfinance, pandas; print('yfinance', yfinance.__version__)"

echo "== files"
mkdir -p "$DST" "$STATIC"
cp "$SRC"/api/engine.py "$SRC"/api/hoodbarons_api.py "$SRC"/api/snapshot.py "$SRC"/api/chain.py "$DST"/
mkdir -p "$STATIC"/cli && cp "$SRC"/cli/hoodbarons.py "$STATIC"/cli/hoodbarons  # no .py: nginx denies script extensions
[ -f "$DST/watchlist.txt" ] || cp "$SRC"/api/watchlist.txt "$DST"/
[ -f "$DST/watchlist_sp500.txt" ] || cp "$SRC"/api/watchlist_sp500.txt "$DST"/ 2>/dev/null || true
cp "$SRC"/web/index.html "$STATIC"/index.html
for f in mascot.png mascot-full.png; do [ -f "$SRC"/web/$f ] && cp "$SRC"/web/$f "$STATIC"/$f; done
chown -R hermes:hermes "$STATIC" 2>/dev/null || true

echo "== service"
cat > /etc/systemd/system/p7-hoodbarons.service <<'EOF'
[Unit]
Description=HOODBarons API (GEX screener, options check, backtester)
After=network.target

[Service]
User=root
WorkingDirectory=/root/hermes/vessels/prometheus7/hoodbarons
Environment=PORT=8031
Environment=HOODBARONS_DB=/root/hermes/vessels/prometheus7/hoodbarons/hoodbarons.db
Environment=ORACLE_URL=https://wanderaround.io/hoodbarons-oracle/chat
ExecStart=/usr/bin/python3 /root/hermes/vessels/prometheus7/hoodbarons/hoodbarons_api.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --now p7-hoodbarons.service >/dev/null
systemctl restart p7-hoodbarons.service
sleep 1
sleep 3; systemctl is-active p7-hoodbarons.service || true

echo "== nginx"
if ! grep -q "zone=hoodb" /etc/nginx/nginx.conf; then
  sed -i 's|limit_req_zone $binary_remote_addr zone=consult:10m rate=30r/m;|&\n    limit_req_zone $binary_remote_addr zone=hoodb:10m rate=120r/m;|' /etc/nginx/nginx.conf
fi
if ! grep -q "/HOODBarons/api/" "$VHOST"; then
  cp "$VHOST" "/root/hermes-vhost.backup.hoodbarons.$(date +%Y%m%d%H%M%S)"
  # insert before the consulting api block
  python3 - "$VHOST" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
block = '''    # ── HOODBarons (prometheus7.com/HOODBarons) ───────────────────────────
    location = /hoodbarons { return 301 /HOODBarons/; }
    location = /HOODBarons { return 301 /HOODBarons/; }
    location ^~ /HOODBarons/api/ {
        limit_req zone=hoodb burst=30 nodelay;
        limit_req_status 429;
        client_max_body_size 256k;
        proxy_pass         http://127.0.0.1:8031;
        proxy_set_header   Host            $host;
        proxy_set_header   X-Real-IP       $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 90s;
        proxy_hide_header  X-Powered-By;
    }

'''
anchor = '    location ^~ /consulting/api/ {'
assert anchor in s, 'anchor missing'
s = s.replace(anchor, block + anchor, 1)
open(p, 'w').write(s)
print('vhost patched')
PY
fi
nginx -t && systemctl reload nginx

echo "== cron"
( crontab -l 2>/dev/null | grep -v hoodbarons/snapshot.py ; echo "35 21 * * 1-5 /usr/bin/python3 $DST/snapshot.py >> $DST/snapshot.log 2>&1" ) | crontab -

echo "== smoke"
curl -s -m 10 http://127.0.0.1:8031/HOODBarons/api/health; echo
curl -s -m 60 "http://127.0.0.1:8031/HOODBarons/api/chain?ticker=SPY&n=1" | python3 -c "import sys,json; j=json.load(sys.stdin); p=j['profile']; print('SPY', j['spot'], 'net', round(p['net_gex']/1e9,2), 'B flip', p['gamma_flip'], 'cw', p['call_wall'], 'pw', p['put_wall'], 'rows', len(j['rows']))"
curl -s -m 60 -X POST http://127.0.0.1:8031/HOODBarons/api/backtest -H 'Content-Type: application/json' -d '{"symbol":"SPY","asset":"equity","strategy":"sma_cross","years":5}' | python3 -c "import sys,json; j=json.load(sys.stdin); print('backtest', j.get('name'), round(j.get('total_return',0)*100,1), 'bh', round(j.get('buy_hold_return',0)*100,1), j.get('error',''))"
curl -s -m 60 -X POST http://127.0.0.1:8031/HOODBarons/api/backtest -H 'Content-Type: application/json' -d '{"symbol":"BTC","asset":"crypto","strategy":"dca","years":2}' | python3 -c "import sys,json; j=json.load(sys.stdin); print('crypto', j.get('name'), round(j.get('total_return',0)*100,1), j.get('error',''))"
curl -s -m 60 -X POST http://127.0.0.1:8031/HOODBarons/api/backtest -H 'Content-Type: application/json' -d '{"symbol":"SPY","asset":"equity","strategy":"wheel","years":3}' | python3 -c "import sys,json; j=json.load(sys.stdin); print('wheel', round(j.get('total_return',0)*100,1), 'trades', j.get('trades'), j.get('error',''))"
curl -s -m 30 -o /dev/null -w "public index %{http_code}\n" https://prometheus7.com/HOODBarons/
curl -s -m 30 -w " public api %{http_code}\n" https://prometheus7.com/HOODBarons/api/health
echo "== done"
