#!/usr/bin/env bash
# box-restic-backup.sh — incremental, deduplicated, encrypted restic backup of the box to Google Drive
# (rclone remote "gdrive:", folder "Grok Bot restic"). Runs ALONGSIDE box-backup.sh; covers the same data
# (plain + secret sets, same excludes, SQLite online-backup snapshots). Secrets are fine here: restic encrypts all.
# Password: $BOX_BACKUP_PASSPHRASE (fed via RESTIC_PASSWORD_COMMAND, never on a command line).
# Recommended: a DEDICATED passphrase file readable only by the backup job's user (chmod 600), e.g.
#   RESTIC_PASSWORD_FILE=~/.config/box-backup/restic.pass   instead of an env var every agent can read.
# Use a different passphrase from the gpg archive one.
# Override repo with RESTIC_REPOSITORY. Prints JSON summary on stdout; logs on stderr.
set -euo pipefail
umask 077
export PATH=$HOME/.local/bin:$PATH
H=/home/box
log() { printf '[box-restic %s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
[[ -n ${BOX_BACKUP_PASSPHRASE:-}${RESTIC_PASSWORD_FILE:-} ]] || { log "BOX_BACKUP_PASSPHRASE / RESTIC_PASSWORD_FILE not set"; exit 2; }
export RESTIC_REPOSITORY=${RESTIC_REPOSITORY:-"rclone:gdrive:Grok Bot restic"}
export RESTIC_PASSWORD_COMMAND='printenv BOX_BACKUP_PASSPHRASE'
if [[ -n ${RESTIC_PASSWORD_FILE:-} ]]; then unset RESTIC_PASSWORD_COMMAND; fi
LOCKDIR=${XDG_RUNTIME_DIR:-$HOME/.cache}/box-restic; mkdir -p "$LOCKDIR"; chmod 700 "$LOCKDIR"   # private dir, not world-writable /tmp
exec 9>"$LOCKDIR/box-restic.lock"; flock -n 9 || { log "another run active"; exit 1; }

STAGE=$(mktemp -d "${TMPDIR:-/tmp}/box-restic-stage.XXXXXXXX"); trap 'rm -rf "$STAGE"' EXIT
# The stage path now changes per run; --group-by host,tags below keeps parent-snapshot selection working.
snapshot_dbs() {  # consistent copies of *.db/*.sqlite under $1 into $STAGE/dbsnap/<abs path>
  [[ -d $1 ]] || return 0
  python3 - "$1" "$STAGE/dbsnap" <<'PY'
import os, sys, sqlite3, shutil
root, dest = sys.argv[1], sys.argv[2]
for d, dirs, files in os.walk(root):
    for f in files:
        if not (f.endswith('.db') or f.endswith('.sqlite')): continue
        src = os.path.join(d, f); out = dest + src
        if f.startswith('search-index.db'): continue
        os.makedirs(os.path.dirname(out), exist_ok=True)
        try:
            with open(src, 'rb') as fh: ok = fh.read(16) == b'SQLite format 3\x00'
        except OSError: continue
        if ok:
            s = sqlite3.connect(src, timeout=60); t = sqlite3.connect(out); s.backup(t); t.close(); s.close()
        else: shutil.copy2(src, out)
PY
}
snapshot_dbs "$H/sand-data"; snapshot_dbs "$H/.beeper/profiles"

PATHS=(/workspace $H/agent-data $H/sand-data $H/reference $H/cli-config $H/.local/share/tg-send $H/.local/share/tg-http
       $H/.config/systemd $H/.bashrc $H/.profile $H/bin
       $H/.local/bin/tg-send $H/.local/bin/beeper-server-keepalive.sh $H/.local/bin/claude $H/.local/bin/tg
       $H/.beeper $H/.config $H/.claude $H/.claude.json $H/.mcp-auth $H/.local/share/tailscale "$STAGE/dbsnap")
EX=()
for p in "${PATHS[@]}"; do [[ -e $p || -L $p ]] && EX+=("$p"); done
cat > "$STAGE/excludes" <<X
/workspace/backups
$H/sand-data/telemetry
$H/sand-data/plugins
$H/sand-data/search-index.db*
$H/sand-data/*.db
$H/sand-data/*.db-wal
$H/sand-data/*.db-shm
$H/sand-data/*.lock
$H/.beeper/apps
$H/.beeper/logs
$H/.beeper/run
$H/.beeper/profiles/*/*/logs
$H/.beeper/profiles/*/*/sdk-tmp
$H/.beeper/profiles/**/*.db
$H/.beeper/profiles/**/*.db-wal
$H/.beeper/profiles/**/*.db-shm
$H/.config/google-chrome/Crash Reports
$H/.local/share/tailscale/dns-cache
node_modules
.venv
venv
__pycache__
.cache
.pytest_cache
.mypy_cache
.ruff_cache
*.sock
tailscaled.log*.txt
X
log "backup -> $RESTIC_REPOSITORY"
rc=0; restic backup --json --quiet --tag box --host grok-bot-box --group-by host,tags --exclude-file "$STAGE/excludes" "${EX[@]}" > "$STAGE/bk.json" || rc=$?
(( rc == 0 || rc == 3 )) || { log "restic backup failed rc=$rc"; exit $rc; }   # 3 = some files unreadable
SUM=$(jq -c 'select(.message_type=="summary")' "$STAGE/bk.json")
log "forget/prune"
restic forget --quiet --tag box --group-by host,tags --keep-daily 14 --keep-weekly 8 --prune >&2
TOTAL=$(restic stats --mode raw-data --json | jq .total_size)
jq -n --argjson s "$SUM" --argjson total "$TOTAL" --argjson rc "$rc" \
  '{snapshot_id:$s.snapshot_id, data_added_bytes:$s.data_added, data_added_packed_bytes:$s.data_added_packed,
    files_new:$s.files_new, files_changed:$s.files_changed, total_bytes_processed:$s.total_bytes_processed,
    repo_total_size_bytes:$total, warnings:($rc==3)}'
