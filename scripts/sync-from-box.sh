#!/usr/bin/env bash
# sync-from-box.sh — report drift between the live box files and this repo's sanitized copies.
#
#   scripts/sync-from-box.sh            report only what changed since the last --ack (silent if nothing)
#   scripts/sync-from-box.sh --all      report every difference, ignoring the baseline
#   scripts/sync-from-box.sh --ack      record the current live files as reviewed (after porting by hand)
#
# It NEVER copies anything into the repo. Live files can contain personal values that the repo
# copies scrub, so every change must be ported by hand (scrubbed per SECURITY.md) and scanned.
# Output is path + count of differing lines only, never file content. No drift = no output, exit 0.
# Drift = exit 3 (so a routine can skip the model call when the exit code is 0).
#
# Rows:  DRIFT <repo_path> <n> lines | MISSING-LIVE <repo_path> | MISSING-REPO <repo_path> | NEW <live_path>
# The baseline (sha256 of live files, kept OUTSIDE the repo) lives in
#   ${SYNC_BASELINE:-$HOME/.local/state/bot-briefs-sync/baseline.tsv}
# NEW scans these live directories (space-separated, override with SYNC_NEW_DIRS):
#   <shared>/watchdogs/bin <shared>/watchdogs/conf.d <shared>/dashboard <shared>/agent-webhook
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
MANIFEST="$ROOT/config/manifest.tsv"
BASE=${SYNC_BASELINE:-$HOME/.local/state/bot-briefs-sync/baseline.tsv}
SHARED=/home/box/agent-data/shared
NEW_DIRS=${SYNC_NEW_DIRS:-"$SHARED/watchdogs/bin $SHARED/watchdogs/conf.d $SHARED/dashboard $SHARED/agent-webhook"}
mode=report
case "${1:-}" in
  "") ;; --all) mode=all ;; --ack) mode=ack ;;
  -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
  *) echo "usage: $0 [--all|--ack]" >&2; exit 2 ;;
esac
[ -r "$MANIFEST" ] || { echo "no manifest: $MANIFEST" >&2; exit 2; }

declare -A base=() known=()
if [ -r "$BASE" ]; then
  while IFS=$'\t' read -r p h; do [ -n "$p" ] && base[$p]=$h; done < "$BASE"
fi
out=() acklines=()
while IFS=$'\t' read -r _phase rp dest _m; do
  case "$_phase" in ''|'#'*) continue ;; esac
  # refuse odd manifest paths (no traversal, absolute destinations only)
  case "$rp" in /*|*..*) echo "skip bad repo_path in manifest" >&2; continue ;; esac
  case "$dest" in /*) ;; *) echo "skip non-absolute destination for $rp" >&2; continue ;; esac
  case "$dest" in *..*) echo "skip bad destination for $rp" >&2; continue ;; esac
  known[$dest]=1
  if [ ! -e "$dest" ]; then out+=("MISSING-LIVE	$rp"); continue; fi
  h=$(sha256sum -- "$dest" | cut -d' ' -f1)
  acklines+=("$dest	$h")
  if [ ! -e "$ROOT/$rp" ]; then out+=("MISSING-REPO	$rp"); continue; fi
  cmp -s -- "$dest" "$ROOT/$rp" && continue
  [ "$mode" = report ] && [ "${base[$dest]:-}" = "$h" ] && continue
  n=$(diff -- "$ROOT/$rp" "$dest" 2>/dev/null | grep -c '^[<>]' || true)
  out+=("DRIFT	$rp	$n lines")
done < "$MANIFEST"

ignore() { # state, databases, logs, backups, caches, local secrets
  case "$1" in
    */.git/*|*/__pycache__/*|*/node_modules/*|*/.venv/*|*/.pytest_cache/*|*/state/*|*/logs/*|*/backups/*) return 0 ;;
    *.db|*.db-*|*.sqlite*|*.log|*.bak|*.bak-*|*.bak.*|*~|*.pyc|*.pid|*.lock|*.tmp|*.swp|*.env|*.env.*|*.local.env) return 0 ;;
    *.pem|*.key|*.p12|*.pfx|*/id_*|*token*|*secret*|*credential*|*.htpasswd|*cookie*|*.Xauthority) return 0 ;;  # never even list
  esac
  return 1
}
shopt -s nocasematch
read -r -a new_dirs <<<"$NEW_DIRS"
G=(git -c core.fsmonitor= -c core.hooksPath=/dev/null)   # live dirs are writable by other agents: no hooks/fsmonitor
for d in "${new_dirs[@]}"; do
  [ -d "$d" ] || continue
  while IFS= read -r -d '' f; do
    ignore "$f" && continue
    [ -n "${known[$f]:-}" ] && continue
    # honour a .gitignore in the live dir's git checkout, if there is one
    if GIT_CONFIG_NOSYSTEM=1 "${G[@]}" -C "$(dirname "$f")" rev-parse --is-inside-work-tree >/dev/null 2>&1 &&
       GIT_CONFIG_NOSYSTEM=1 "${G[@]}" -C "$(dirname "$f")" check-ignore -q -- "$f" 2>/dev/null; then continue; fi
    h=$(sha256sum -- "$f" | cut -d' ' -f1)
    acklines+=("$f	$h")
    [ "$mode" = report ] && [ "${base[$f]:-}" = "$h" ] && continue
    out+=("NEW	$f")
  done < <(find "$d" -type f -print0 2>/dev/null)
done

if [ "$mode" = ack ]; then
  umask 077; mkdir -p "$(dirname "$BASE")"
  printf '%s\n' "${acklines[@]}" > "$BASE.tmp" && mv -f "$BASE.tmp" "$BASE"
  echo "baseline recorded: ${#acklines[@]} live files" >&2
  exit 0
fi
[ ${#out[@]} -eq 0 ] && exit 0
printf '%s\n' "${out[@]}"
exit 3
