#!/usr/bin/env bash
# HOODBarons Oracle7 instance — Box C. Compiles the finance corpus into its OWN pair of
# builds beside the m13e7 ones (never touching them), runs a separate harness on :8098,
# and exposes it at https://wanderaround.io/hoodbarons-oracle/chat.
# Usage: boxc_oracle_install.sh /root/hoodbarons_upload   (STEP=inspect to only print compiler usage)
set -euo pipefail
SRC="${1:-/root/hoodbarons_upload}"
STEP="${STEP:-all}"
ROOT=/opt/oracle-clm/fable-content
CORPUS_DIR=/opt/oracle-clm/fable-content/hoodbarons-corpus
PHASE=$ROOT/oracle-m-series-2026-08-15
BENCH_BASE=m13e7_dbpedia_openstax_full_fable_bench_a_v1
WIKI_BASE=m13e7_wiki_openstax_full_fable_build_a_v1
BENCH_OUT_NAME=hoodbarons_dbpedia_openstax_fable_bench_a_v1
WIKI_OUT_NAME=hoodbarons_wiki_openstax_fable_build_a_v1
BENCH_OUT=$PHASE/$BENCH_OUT_NAME
WIKI_OUT=$PHASE/$WIKI_OUT_NAME
STATE=/opt/oracle-clm/harness_hoodbarons_state
COMPILER=/tmp/compile_fable_corpus_v3.py
FINALIZE=/tmp/finalize_fable_build_v1.py

mkdir -p "$CORPUS_DIR"
cp "$SRC"/corpus/hoodbarons_d1.jsonl "$CORPUS_DIR"/
python3 -c "
import json,collections
recs=[json.loads(l) for l in open('$CORPUS_DIR/hoodbarons_d1.jsonl') if l.strip()]
al=collections.Counter(a for r in recs for a in set(x.lower() for x in [r['title']]+r['aliases']))
dup=[a for a,n in al.items() if n>1]
print('corpus articles',len(recs),'sentences',sum(len(r['sentences']) for r in recs),'alias dups',dup)
assert not dup
"

if [ "$STEP" = "inspect" ]; then
  echo "== compiler header"; sed -n 1,60p $COMPILER | grep -nE "FABLE_|BASE|OUT|Usage|usage|argv|env" | head -20
  echo "== finalize header"; sed -n 1,30p $FINALIZE | grep -nE "argv|Usage|usage|def main" | head
  echo "== base sizes"; du -sh "$PHASE/$BENCH_BASE" "$PHASE/$WIKI_BASE"; df -h / | tail -1
  exit 0
fi

echo "== sizes"; du -sh "$PHASE/$BENCH_BASE" "$PHASE/$WIKI_BASE"; df -h / | tail -1
echo "== compile bench (dbpedia+openstax) -> $BENCH_OUT_NAME"
FABLE_BASE="$BENCH_BASE" FABLE_OUT="$BENCH_OUT_NAME" python3 $COMPILER "$CORPUS_DIR"/hoodbarons_d1.jsonl 2>&1 | tail -8
echo "== compile wiki (simplewiki+openstax) -> $WIKI_OUT_NAME"
FABLE_BASE="$WIKI_BASE" FABLE_OUT="$WIKI_OUT_NAME" python3 $COMPILER "$CORPUS_DIR"/hoodbarons_d1.jsonl 2>&1 | tail -8
python3 $FINALIZE "$WIKI_OUT" 2>&1 | tail -3 || true
ls "$BENCH_OUT" "$WIKI_OUT" | head -12

echo "== harness unit :8098"
cat > /etc/systemd/system/oracle-harness-hoodbarons.service <<EOF
[Unit]
Description=Oracle7 HOODBarons instance (finance corpus, own builds, :8098)
After=network.target

[Service]
WorkingDirectory=/opt/oracle-clm/wander-train
Environment=PYTHONHASHSEED=0
Environment=ORACLE_BENCH_BUILD=$BENCH_OUT/wikipedia_specialist.sqlite3
Environment=ORACLE_WIKIPEDIA_BUILD=$WIKI_OUT
Environment=ORACLE_STATE_ROOT=$STATE
Environment=PORT=8098
ExecStart=/usr/bin/python3 /opt/oracle-clm/wander-train/oracle_space_app_v21_autocorrect.py
Restart=always
RestartSec=10
OOMScoreAdjust=-500

[Install]
WantedBy=multi-user.target
EOF
mkdir -p "$STATE"
systemctl daemon-reload
systemctl enable --now oracle-harness-hoodbarons.service >/dev/null
systemctl restart oracle-harness-hoodbarons.service
sleep 30
sleep 3; systemctl is-active oracle-harness-hoodbarons.service || true

echo "== nginx (wander vhost)"
V=/etc/nginx/sites-enabled/wander
if ! grep -q "/hoodbarons-oracle/chat" "$V"; then
  cp "$V" "/root/wander-vhost.backup.hoodbarons.$(date +%Y%m%d%H%M%S)"
  python3 - "$V" <<'PY'
import sys,re
p=sys.argv[1]; s=open(p).read()
m=re.search(r'(    location = /oracle7-public/chat \{.*?\n    \}\n)', s, re.S)
assert m, 'oracle7-public/chat block not found'
blk=m.group(1).replace('/oracle7-public/chat','/hoodbarons-oracle/chat').replace('127.0.0.1:8097/chat','127.0.0.1:8098/chat')
s=s.replace(m.group(1), m.group(1)+'\n    # HOODBarons Oracle7 instance (finance corpus) -> :8098\n'+blk,1)
open(p,'w').write(s); print('wander vhost patched')
PY
fi
nginx -t && systemctl reload nginx

echo "== probes"
for q in "what is gamma exposure" "what is a gamma flip" "what is the wheel strategy" "what is HOODBarons"; do
  printf '%s -> ' "$q"
  curl -s -m 60 -X POST 127.0.0.1:8098/chat -H 'Content-Type: application/json' -d "{\"text\":\"$q\"}" | python3 -c "import sys,json; j=json.load(sys.stdin); print(j.get('status'), '|', j.get('response','')[:160].replace(chr(10),' '), '| refs', len(j.get('references',[])))"
done
curl -s -m 60 -X POST https://wanderaround.io/hoodbarons-oracle/chat -H 'Content-Type: application/json' -d '{"text":"what is a call wall"}' | head -c 300; echo
echo "== staging :8097 untouched"; systemctl is-active oracle-harness-staging
echo "== done"
