#!/usr/bin/env bash
# Phase 1: directory layout. ROOT=/tmp/stage stages under another root.
set -euo pipefail
R="${ROOT:-}"
for d in /home/box/bin /home/box/.local/bin /home/box/src/one-offs/cos-gather \
  /home/box/agent-data/shared/dashboard/data /home/box/agent-data/shared/dashboard/logs /home/box/agent-data/shared/refs \
  /home/box/agent-data/shared/watchdogs/bin /home/box/agent-data/shared/watchdogs/conf.d /home/box/agent-data/shared/agent-webhook \
  /home/box/agent-data/shared/briefs-mirror /home/box/.local/state/watchdogs /home/box/.local/state/cos-gather \
  /home/box/.config/monit /home/box/.config/agent-webhook /home/box/.local/share/tailscale /workspace; do
  mkdir -p "$R$d"; echo "ok $R$d"; done
chmod 700 "$R/home/box/.config/agent-webhook" "$R/home/box/.config/monit"
[ -e "$R/home/box/agent-data/shared/source-health.json" ] || echo '{}' > "$R/home/box/agent-data/shared/source-health.json"
