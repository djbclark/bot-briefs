#!/usr/bin/env bash
# RETIRED 2026-10-24: replaced by monit (/home/box/agent-data/shared/watchdogs.md). Exits immediately.
exit 0
# Keeps the Bot Briefs dashboard (uvicorn on 127.0.0.1:${BRIEFS_PORT:-8642}) running.
# Single instance via flock. No systemd/cron on this box; started by dashboard-ensure
# (hooked from ~/.bashrc and ~/.profile). Also re-applies the tailscale serve mapping if it vanished.
set -u
D=/home/box/agent-data/shared/dashboard
PORT=${BRIEFS_PORT:-8642}
RUN=/home/box/.local/share/briefs-dashboard
LOG=$RUN/keepalive.log
TS_SOCK=/home/box/.local/share/tailscale/tailscaled.sock
mkdir -p "$RUN" "$D/logs"
exec 9>"$RUN/keepalive.lock"
flock -w 5 9 || exit 0
echo $$ > "$RUN/keepalive.pid"
log() { echo "$(date -Is) $*" >> "$LOG"; }
trim() { for f in "$LOG" "$D/logs/server.log" "$D/logs/access.log" "$D/logs/import.log"; do [ -f "$f" ] && [ "$(stat -c %s "$f")" -gt 5000000 ] && tail -c 1000000 "$f" > "$f.tmp" && mv "$f.tmp" "$f"; done; }
start_server() {
  log "starting uvicorn on 127.0.0.1:$PORT"
  cd "$D" || return 1
  # Separate statement so the backgrounded job is setsid itself (not a subshell holding the lock fd).
  setsid nohup "$D/.venv/bin/python" -m uvicorn briefs.web:app --host 127.0.0.1 --port "$PORT" \
     >> "$D/logs/server.log" 2>&1 < /dev/null 9>&- &
  echo $! > "$RUN/server.pid"
  sleep 3
}
ensure_serve() {
  # Only add our mapping when serve has no config for it; never touches funnel.
  if [ -S "$TS_SOCK" ] && ! tailscale --socket="$TS_SOCK" serve status 2>/dev/null | grep -q "127.0.0.1:$PORT"; then
    if tailscale --socket="$TS_SOCK" status --peers=false >/dev/null 2>&1; then
      log "serve mapping missing; re-applying https:443 / -> 127.0.0.1:$PORT"
      timeout 30 tailscale --socket="$TS_SOCK" serve --bg --https=443 "http://127.0.0.1:$PORT" >> "$LOG" 2>&1
    fi
  fi
}
fails=0; n=0
log "keepalive started (pid $$)"
nightly_import() {
  # Safety-net re-import of the existing report files, once per day after 03:45 ET (idempotent).
  local today hm
  today=$(TZ=America/New_York date +%F); hm=$(TZ=America/New_York date +%H%M)
  [ "$hm" -ge 0345 ] || return 0
  [ "$(cat "$RUN/last-import" 2>/dev/null)" = "$today" ] && return 0
  echo "$today" > "$RUN/last-import"
  log "nightly import starting (background)"
  ( cd "$D" && timeout 1200 "$D/.venv/bin/python" -m briefs.importer >> "$D/logs/import.log" 2>&1
    log "nightly import finished (exit $?)" ) 9>&- < /dev/null &
}
while true; do
  if ! curl -s -m 5 -o /dev/null -f "http://127.0.0.1:$PORT/healthz"; then
    fails=$((fails+1))
    if [ $fails -ge 2 ] || ! pgrep -u "$(id -u)" -f "uvicorn briefs.web:app" >/dev/null; then
      pkill -u "$(id -u)" -f "uvicorn briefs.web:app"; sleep 1; start_server; fails=0
    fi
  else
    fails=0
  fi
  n=$((n+1)); [ $((n % 15)) -eq 1 ] && ensure_serve
  nightly_import
  trim
  sleep 20 9>&-   # do not let the sleep child hold the lock if this loop is killed
done
