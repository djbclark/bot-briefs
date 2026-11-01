#!/usr/bin/env bash
# box-backup.sh — back up hard-to-rebuild state of Grok Bot's box to /workspace/backups/.
#
#  * PLAIN archive  : box-backup-YYYYMMDD-HHMM.tar.{zst|gz}   (non-secret state + MANIFEST)
#  !! The PLAIN archive is NOT encrypted. Recommended: retire it in favour of restic
#     (bin/box-restic-backup.sh encrypts everything), or set BOX_BACKUP_ENCRYPT_PLAIN=1 to gpg it too.
#  !! Use a dedicated passphrase file readable only by the backup job (chmod 600,
#     BOX_BACKUP_PASSPHRASE_FILE) rather than an env var every agent can read.
#  * SECRET archive : box-secrets-YYYYMMDD-HHMM.tar.{zst|gz}.gpg  (AES256, symmetric)
#                     only when $BOX_BACKUP_PASSPHRASE is set & non-empty.
#  * keeps the 3 newest local archives of each kind (plus their .part-* split pieces)
#  * prints a JSON summary on stdout (all logs go to stderr)
#
# Restore:  plain   ->  tar -xpf box-backup-*.tar.gz -C /          (or: zstd -dc ... | tar -xpf - -C /)
#           secrets ->  gpg -d box-secrets-*.gpg | tar -xzpf - -C /  (use tar --zstd for .zst)
#           (cat the .part-* pieces together first if the archive was split)
# Live SQLite DBs are snapshotted with the SQLite online-backup API, so restored DBs are consistent.
set -euo pipefail
umask 077

OUT=${BOX_BACKUP_OUT:-/workspace/backups}
TS=$(date +%Y%m%d-%H%M)
KEEP=3
H=/home/box
log() { printf '[box-backup %s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }

mkdir -p "$OUT"; chmod 700 "$OUT"
exec 9>"$OUT/.lock"
flock -n 9 || { log "another backup is running"; exit 1; }

# ---- compressor --------------------------------------------------------------------------------
if command -v zstd >/dev/null 2>&1; then EXT=tar.zst; COMP=(zstd -q -T0 -10 -c)
elif command -v pigz >/dev/null 2>&1; then EXT=tar.gz; COMP=(pigz -6 -c)
else EXT=tar.gz; COMP=(gzip -6 -c); fi
# Dedicated passphrase file (chmod 600, owned by the backup job's user) preferred over a shared env var.
if [[ -n ${BOX_BACKUP_PASSPHRASE_FILE:-} && -r $BOX_BACKUP_PASSPHRASE_FILE ]]; then
  BOX_BACKUP_PASSPHRASE=$(<"$BOX_BACKUP_PASSPHRASE_FILE")
fi

STAGE=$(mktemp -d "$OUT/.stage.XXXXXX")
cleanup() { rm -rf "$STAGE"; rm -f "$OUT"/*.partial; }
trap cleanup EXIT

# tar exit 1 == "file changed while reading" -> acceptable for a live system
run_tar() { local rc=0; tar "$@" || rc=$?; if (( rc > 1 )); then log "tar failed rc=$rc"; return "$rc"; fi; return 0; }

# SQLite-consistent snapshot of every *.db / *.sqlite under $1 into $STAGE/$2/<abs path>
snapshot_dbs() {
  local root=$1 dest=$STAGE/$2
  [[ -d $root ]] || return 0
  python3 - "$root" "$dest" <<'PY'
import os, sys, sqlite3, shutil
root, dest = sys.argv[1], sys.argv[2]
for d, dirs, files in os.walk(root):
    for f in files:
        if not (f.endswith('.db') or f.endswith('.sqlite')): continue
        src = os.path.join(d, f); out = dest + src
        os.makedirs(os.path.dirname(out), exist_ok=True)
        try:
            with open(src, 'rb') as fh: is_sqlite = fh.read(16) == b'SQLite format 3\x00'
        except OSError: continue
        if is_sqlite:
            s = sqlite3.connect(src, timeout=60); t = sqlite3.connect(out)
            s.backup(t); t.close(); s.close()
        else:
            shutil.copy2(src, out)
PY
}

# ================================ PLAIN ARCHIVE ===============================================
PLAIN="$OUT/box-backup-$TS.$EXT"
MAN_DIR="$STAGE/plain$H/.box-backup-manifest"; mkdir -p "$MAN_DIR"
MAN="$MAN_DIR/MANIFEST.md"

# Files in /workspace that look like they contain real credentials are excluded from the plain archive.
# Extended: Telegram bot tokens, Slack/Discord webhook URLs, webhook keys, generic Bearer/token/Basic auth headers.
SECRET_RE='(sk-ant-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,}|gho_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|xox[bpas]-[0-9A-Za-z-]{20,}|AIza[0-9A-Za-z_-]{35}|ya29\.[0-9A-Za-z_-]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|tskey-[a-z]+-[A-Za-z0-9]{10,}|"(access|refresh)_token"\s*:\s*"[A-Za-z0-9._-]{30,}"|[0-9]{8,10}:AA[0-9A-Za-z_-]{30,}|https://hooks\.slack\.com/services/[A-Za-z0-9/]{20,}|https://discord(app)?\.com/api/webhooks/[0-9]+/[A-Za-z0-9_-]{20,}|AGENT_WEBHOOK_[A-Z_]*KEY=[^[:space:]<]{16,}|[Bb]earer[[:space:]]+[A-Za-z0-9._~+/=-]{24,}|[Aa]uthorization:[[:space:]]*(token|Basic)[[:space:]]+[A-Za-z0-9._~+/=-]{20,})'
: > "$STAGE/ws-secret-excludes"
if command -v rg >/dev/null 2>&1; then
  rg -l --hidden --no-ignore --max-filesize 50M -g '!backups/**' -g '!**/node_modules/**' -g '!**/.venv/**' \
     -e "$SECRET_RE" /workspace 2>/dev/null | sed 's#^/##' | sort > "$STAGE/ws-secret-excludes" || true
fi

{
  echo "# Grok Bot box backup manifest"
  echo "- created: $(date -Is)   host: $(hostname)   $(. /etc/os-release; echo "$PRETTY_NAME")"
  echo "- archive: $(basename "$PLAIN")"
  echo
  echo "## Extra tools & versions"
  ver() { local n=$1; shift; printf -- '- %s: ' "$n"; (timeout 30 "$@" 2>&1 | head -n 3 | tr '\n' ' ') || echo "(not installed)"; echo; }
  ver claude claude --version
  ver "tg (pytgcli)" uv tool list
  ver tailscale tailscale version
  ver "beeper CLI" beeper --version
  ver node node --version
  ver npm npm --version
  ver bun bun --version
  ver uv uv --version
  ver gh gh --version
  ver python3 python3 --version
  ver google-chrome google-chrome --version
  echo
  echo "## uv tool list --show-paths"; echo '```'; uv tool list --show-paths 2>&1 || true; echo '```'
  echo "## npm -g list (prefix: $(npm config get prefix 2>/dev/null))"; echo '```'; npm ls -g --depth=0 2>&1 || true; echo '```'
  echo "## apt packages from added repos"; echo '```'; dpkg-query -W -f='${Package} ${Version}\n' tailscale google-chrome-stable 2>&1 || true; echo '```'
  echo "## /etc/apt/sources.list.d"
  for f in /etc/apt/sources.list.d/*; do [[ -f $f ]] || continue; echo "### $f"; echo '```'; cat "$f"; echo '```'; done
  echo "## keyrings in /usr/share/keyrings & /etc/apt/keyrings (non-Debian)"; echo '```'
  ls -1 /usr/share/keyrings /etc/apt/keyrings 2>/dev/null | grep -v '^debian-' || true; echo '```'
  echo "## ~/.local/bin"; echo '```'; ls -la "$H/.local/bin" 2>&1; echo '```'
  echo "## user-added /usr/local/bin entries (owned by box or symlinks into /home/box)"; echo '```'
  find /usr/local/bin -maxdepth 1 \( -user box -o -lname '/home/box/*' \) -printf '%p -> %l\n' 2>/dev/null || true; echo '```'
  echo "## Running user services at backup time"; echo '```'
  pgrep -af 'tailscaled|beeper-server|keepalive' 2>/dev/null | cut -c1-250 || true; echo '```'
  cat <<'NOTES'

## How things were installed / how to reinstall
- **Claude Code**: `npm config set prefix ~/.local` then `npm install -g @anthropic-ai/claude-code`
  (bin symlink ~/.local/bin/claude; root symlink /usr/local/bin/claude -> ~/.local/bin/claude). Auth: `claude setup-token`
  or restore ~/.claude + ~/.claude.json from the secrets archive.
- **tgcli (`tg`)**: `uv tool install pytgcli` (venv in ~/.local/share/uv/tools/pytgcli; symlink ~/.local/bin/tg,
  /usr/local/bin/tg). Config/session: ~/.config/tgcli/{config.toml,session} (secrets archive). Companion sender:
  ~/.local/bin/tg-send + ~/.local/share/tg-send/tg_send.py (plain archive); HTTP transport shim in ~/.local/share/tg-http
  (has its own install.sh).
- **Tailscale**: apt repo `https://pkgs.tailscale.com/stable/debian trixie main` with key
  /usr/share/keyrings/tailscale-archive-keyring.gpg (`curl -fsSL https://pkgs.tailscale.com/stable/debian/trixie.noarmor.gpg | sudo tee /usr/share/keyrings/tailscale-archive-keyring.gpg`;
  `curl -fsSL https://pkgs.tailscale.com/stable/debian/trixie.tailscale-keyring.list | sudo tee /etc/apt/sources.list.d/tailscale.list`;
  `sudo apt-get update && sudo apt-get install -y tailscale`). Runs unprivileged (no systemd):
  `tailscaled --tun=userspace-networking --socks5-server=localhost:1055 --outbound-http-proxy-listen=localhost:1056 --statedir=$HOME/.local/share/tailscale --socket=$HOME/.local/share/tailscale/tailscaled.sock`
  then `tailscale --socket=... up --accept-dns=false --hostname=grok-bot-box`. State dir is in the secrets archive.
- **Beeper CLI**: `@beeper/cli` standalone binary at ~/.local/bin/beeper (installed from a beeper-cli-*.tar.gz release tarball).
  Beeper Server app is downloaded by the CLI into ~/.beeper/apps (excluded, re-download with `beeper targets ...`);
  profile/DB in ~/.beeper/profiles (secrets archive). Keepalive loop: ~/.local/bin/beeper-server-keepalive.sh
  (NOTE: it calls /usr/local/bin/beeper, which may not exist — the CLI lives at ~/.local/bin/beeper).
- **google-chrome**: provided by the box image (google-chrome.sources), not user-installed.
- **bun / uv**: provided by the box image (/usr/local/bin).
NOTES
  echo
  echo "## /workspace files excluded because they matched credential patterns"; echo '```'; cat "$STAGE/ws-secret-excludes"; echo '```'
} > "$MAN" 2>&1

# consistent SQLite snapshots of agent-data (originals excluded below)
snapshot_dbs "$H/sand-data" plain
find "$STAGE/plain" -path '*search-index.db' -delete 2>/dev/null || true

PLAIN_ITEMS=(workspace home/box/agent-data home/box/sand-data home/box/reference home/box/cli-config
             home/box/.local/share/tg-send home/box/.local/share/tg-http home/box/.config/systemd
             home/box/.bashrc home/box/.profile)
[[ -d $H/bin ]] && PLAIN_ITEMS+=(home/box/bin)
for f in tg-send beeper-server-keepalive.sh claude tg; do [[ -e $H/.local/bin/$f || -L $H/.local/bin/$f ]] && PLAIN_ITEMS+=("home/box/.local/bin/$f"); done
EX=()
for p in "${PLAIN_ITEMS[@]}"; do [[ -e /$p || -L /$p ]] && EX+=("$p"); done

log "building plain archive"
run_tar -cf "$STAGE/plain.tar" -C / --warning=no-file-changed --warning=no-file-removed --exclude-from="$STAGE/ws-secret-excludes" \
  --anchored --exclude=workspace/backups --exclude=workspace/trail-519 --exclude=workspace/libkrun --exclude=workspace/openafs-17048 --exclude=workspace/skillopt-venv \
  --exclude='home/box/sand-data/box-secrets.json' --exclude='home/box/sand-data/host-secrets.json' \
  --exclude='home/box/sand-data/gateway.json' --exclude='home/box/sand-data/teach-queue-key.json' \
  --exclude='home/box/sand-data/source-map.json' --exclude='home/box/sand-data/chrome-cookie-seed.json' \
  --exclude='home/box/sand-data/sand-statsig-bootstrap.json' --exclude='home/box/sand-data/telemetry' \
  --exclude='home/box/sand-data/plugins' --exclude='home/box/sand-data/search-index.db*' \
  --exclude='home/box/sand-data/*.db' --exclude='home/box/sand-data/*.db-wal' --exclude='home/box/sand-data/*.db-shm' \
  --exclude='home/box/sand-data/*.lock' \
  --no-anchored --exclude=node_modules --exclude=.venv --exclude=venv --exclude=__pycache__ --exclude=.cache \
  --exclude=.pytest_cache --exclude=.mypy_cache --exclude=.ruff_cache --exclude='*.sock' \
  "${EX[@]}"
run_tar -rf "$STAGE/plain.tar" -C "$STAGE/plain" home
if [[ ${BOX_BACKUP_ENCRYPT_PLAIN:-0} == 1 && -n ${BOX_BACKUP_PASSPHRASE:-} ]]; then
  PLAIN="$PLAIN.gpg"; rm -rf "$STAGE/gnupg-p"; mkdir -m 700 "$STAGE/gnupg-p"
  printf '%s' "$BOX_BACKUP_PASSPHRASE" | GNUPGHOME="$STAGE/gnupg-p" gpg --batch --yes --quiet --symmetric \
      --cipher-algo AES256 --compress-algo none --pinentry-mode loopback --passphrase-fd 0 \
      --output "$PLAIN.partial" <("${COMP[@]}" "$STAGE/plain.tar")
else
  "${COMP[@]}" "$STAGE/plain.tar" > "$PLAIN.partial"
fi
mv -f "$PLAIN.partial" "$PLAIN"; rm -f "$STAGE/plain.tar"
log "plain archive: $PLAIN ($(du -h "$PLAIN" | cut -f1))"

# ================================ SECRET ARCHIVE ==============================================
SECRET=""; SECRET_SKIPPED=true; SKIP_REASON="BOX_BACKUP_PASSPHRASE not set"
if [[ -n ${BOX_BACKUP_PASSPHRASE:-} ]]; then
  SECRET_SKIPPED=false; SKIP_REASON=""
  SECRET="$OUT/box-secrets-$TS.$EXT.gpg"
  snapshot_dbs "$H/.beeper/profiles" secret
  S_ITEMS=()
  for p in home/box/.beeper home/box/.config home/box/.claude home/box/.claude.json home/box/.mcp-auth \
           home/box/.local/share/tailscale home/box/sand-data/box-secrets.json home/box/sand-data/host-secrets.json \
           home/box/sand-data/gateway.json home/box/sand-data/teach-queue-key.json home/box/sand-data/source-map.json; do
    [[ -e /$p ]] && S_ITEMS+=("$p")
  done
  log "building secret archive"
  run_tar -cf "$STAGE/secret.tar" -C / --warning=no-file-changed --warning=no-file-removed \
    --anchored --exclude=home/box/.beeper/apps --exclude=home/box/.beeper/logs --exclude=home/box/.beeper/run \
    --exclude='home/box/.beeper/profiles/*/*/logs' --exclude='home/box/.beeper/profiles/*/*/sdk-tmp' \
    --exclude='home/box/.beeper/profiles/*.db' --exclude='home/box/.beeper/profiles/*.db-wal' \
    --exclude='home/box/.beeper/profiles/*.db-shm' \
    --exclude='home/box/.config/google-chrome/Crash Reports' --exclude='home/box/.local/share/tailscale/dns-cache' \
    --no-anchored --exclude='*.sock' --exclude='tailscaled.log*.txt' \
    "${S_ITEMS[@]}"
  [[ -d $STAGE/secret$H ]] && run_tar -rf "$STAGE/secret.tar" -C "$STAGE/secret" home
  # passphrase goes to gpg on fd 0 (printf is a builtin: never on a command line / in ps); data via process substitution
  rm -rf "$STAGE/gnupg"; mkdir -m 700 "$STAGE/gnupg"
  printf '%s' "$BOX_BACKUP_PASSPHRASE" | GNUPGHOME="$STAGE/gnupg" gpg --batch --yes --quiet --symmetric \
      --cipher-algo AES256 --compress-algo none --pinentry-mode loopback --passphrase-fd 0 \
      --output "$SECRET.partial" <("${COMP[@]}" "$STAGE/secret.tar" || touch "$STAGE/comp.failed")
  [[ ! -e $STAGE/comp.failed ]] || { log "compression of secret archive failed"; exit 1; }
  mv -f "$SECRET.partial" "$SECRET"
  rm -f "$STAGE/secret.tar"; rm -rf "$STAGE/secret"
  log "secret archive: $SECRET ($(du -h "$SECRET" | cut -f1))"
else
  log "secret archive skipped: $SKIP_REASON"
fi

# ================================ RETENTION ===================================================
prune() {  # $1 = glob prefix, $2 = suffix regex
  local f
  ls -1t "$OUT"/$1 2>/dev/null | grep -E "$2" | tail -n +$((KEEP + 1)) | while IFS= read -r f; do
    log "pruning $f"; rm -f -- "$f" "$f".part-*
  done || true
}
prune 'box-backup-*' '/box-backup-[0-9]{8}-[0-9]{4}\.tar\.(zst|gz)(\.gpg)?$'
prune 'box-secrets-*' '/box-secrets-[0-9]{8}-[0-9]{4}\.tar\.(zst|gz)\.gpg$'

# ================================ SUMMARY =====================================================
size() { [[ -n $1 && -f $1 ]] && stat -c %s "$1" || echo 0; }
jq -n --arg plain "$PLAIN" --argjson plain_bytes "$(size "$PLAIN")" \
      --arg secret "$SECRET" --argjson secret_bytes "$(size "$SECRET")" \
      --argjson skipped "$SECRET_SKIPPED" --arg reason "$SKIP_REASON" --arg ts "$TS" \
      --argjson ws_excl "$(wc -l < "$STAGE/ws-secret-excludes")" \
  '{timestamp:$ts, plain:{path:$plain, bytes:$plain_bytes, human:(($plain_bytes/1048576*10|floor)/10|tostring+" MiB")},
    secret:(if $skipped then null else {path:$secret, bytes:$secret_bytes, human:(($secret_bytes/1048576*10|floor)/10|tostring+" MiB")} end),
    secret_skipped:$skipped, secret_skip_reason:(if $skipped then $reason else null end),
    workspace_files_excluded_as_secret:$ws_excl,
    new_files:([$plain] + (if $skipped then [] else [$secret] end))}'
