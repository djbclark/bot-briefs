#!/usr/bin/env bash
# Read-only checks per phase. Usage: scripts/verify.sh <phase|all> [dashboard-url]
# e.g. scripts/verify.sh 10 https://YOUR-BOX.YOUR-TAILNET.ts.net
P="${1:-all}"; URL="${2:-https://YOUR-BOX.YOUR-TAILNET.ts.net}"
run() {
case "$1" in
  0)  # Phase 0 check: box identity and shared folder.
    echo "user=$(whoami) home=$HOME"
    ls -ld /home/box/agent-data/shared && echo OK-shared || echo "MISSING shared folder"
    ;;
  1)  # Phase 1 check: tools on PATH and directories.
    for t in python3 sqlite3 git curl monit uv restic rclone tailscale tailscaled; do printf '%-12s ' $t; command -v $t || echo MISSING; done
    for d in /home/box/bin /home/box/agent-data/shared/refs /home/box/agent-data/shared/watchdogs /home/box/.local/state/watchdogs; do [ -d $d ] && echo "ok $d" || echo "MISSING $d"; done
    ;;
  2)  # Phase 2 check: dashboard answers locally.
    curl -fsS -m 5 http://127.0.0.1:8642/healthz && echo " <- local healthz" || echo "not answering on 127.0.0.1:8642 (scripts/start-dashboard-foreground.sh, or monit)"
    ls -la /home/box/agent-data/shared/dashboard/data/ 2>/dev/null || true
    ;;
  3)  # Phase 3 check: brief CLI works.
    /home/box/bin/brief --help | head -20
    /home/box/bin/brief items --open --all --json | head -c 400; echo
    ;;
  4)  # Phase 4 check: helper scripts present (does not consume a Ref).
    for f in /home/box/bin/brief /home/box/bin/ref /home/box/bin/ref-append /home/box/bin/report-to-items /home/box/bin/cos-gather ~/.local/bin/notify-bot; do [ -x $f ] && echo "ok $f" || echo "MISSING $f"; done
    ~/.local/bin/notify-bot --list || true
    ;;
  8)  # Phase 8 check: opencli sources on the box route.
    command -v opencli >/dev/null || { echo "opencli not installed (fine if you skip Saner/Shortwave)"; exit 0; }
    timeout 40 opencli doctor | head -20
    timeout 40 opencli saner status -f json | head -c 300; echo
    timeout 40 opencli shortwave status -f json | head -c 300; echo
    ;;
  9)  # Phase 9 check: acp-run and installed agents.
    command -v acp-run && acp-run --list || echo "acp-run missing"
    ;;
  10)  # Phase 10 check: tailscaled, Serve mapping, no Funnel, URL via SOCKS. Usage: just verify-10 https://BOX.TAILNET.ts.net
    S=/home/box/.local/share/tailscale/tailscaled.sock
    tailscale --socket=$S status | head -5
    tailscale --socket=$S serve status
    if tailscale --socket=$S funnel status 2>/dev/null | grep -qi "funnel on"; then echo "WARNING: FUNNEL IS ON, turn it off"; else echo "ok: no funnel"; fi
    curl -fsS -m 15 --socks5-hostname localhost:1055 "$URL/healthz" && echo " <- tailnet healthz via SOCKS"
    ;;
  11)  # Phase 11 check: monit summary and recent watchdog log.
    monit -c ~/.config/monit/monitrc summary || echo "monit not running: scripts/start-watchdogs.sh"
    tail -5 ~/.local/state/watchdogs/watchdog.log 2>/dev/null || true
    ;;
  12)  # Phase 12 check: restic snapshots (needs box secret BOX_BACKUP_PASSPHRASE and the gdrive rclone remote).
    export RESTIC_PASSWORD_COMMAND='printenv BOX_BACKUP_PASSPHRASE'
    restic -r "rclone:gdrive:Grok Bot restic" snapshots --latest 3 || echo "restic not configured yet"
    ;;
  13)  # Phase 13 check: box-side mirror status (optional phase).
    cat /home/box/agent-data/shared/briefs-mirror/status.json 2>/dev/null || echo "mirror not set up (optional)"
    tail -5 /home/box/agent-data/shared/briefs-mirror/lag.log 2>/dev/null || true
    ;;
  *) echo "no automated check for phase $1 (see docs)";;
esac
}
if [ "$P" = all ]; then for p in 0 1 2 3 4 8 9 11 12 13; do echo "=== phase $p"; run $p; done; else run "$P"; fi
