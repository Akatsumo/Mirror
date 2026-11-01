#!/usr/bin/env bash
# Boot: optional upstream update, optional local engines (single-container mode), then the bot.
set -euo pipefail
cd "$(dirname "$0")"
[ -f config.env ] && set -a && . ./config.env && set +a
python3 update.py || true
if [ "${START_LOCAL_ENGINES:-0}" = "1" ]; then
  mkdir -p "${DOWNLOAD_DIR:-downloads}"
  pgrep -x aria2c >/dev/null || aria2c --enable-rpc --rpc-listen-all=false --rpc-listen-port="${ARIA2_PORT:-6800}" \
    ${ARIA2_SECRET:+--rpc-secret="$ARIA2_SECRET"} --daemon=true --max-connection-per-server=16 --split=16 \
    --min-split-size=4M --continue=true --allow-overwrite=true --bt-enable-lpd=true --seed-time=0 --quiet
  pgrep -x qbittorrent-nox >/dev/null || qbittorrent-nox -d --webui-port="${QBIT_PORT:-8090}" --profile="$PWD/data" \
    --confirm-legal-notice 2>/dev/null || qbittorrent-nox -d --webui-port="${QBIT_PORT:-8090}" --profile="$PWD/data"
fi
exec python3 -m Mirror
