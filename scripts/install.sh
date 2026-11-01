#!/usr/bin/env bash
# Copy repo files to their box destinations, per phase, from config/manifest.tsv.
# DRY RUN by default. Idempotent: existing files are skipped unless --force (which keeps a .bak-<timestamp> copy).
# Usage: scripts/install.sh --phase N [--apply] [--force] [--root /tmp/stage]
#        scripts/install.sh --list            (phases and file counts)
set -euo pipefail
cd "$(dirname "$0")/.."
PHASE=""; APPLY=0; FORCE=0; ROOT=""
while [ $# -gt 0 ]; do case "$1" in
  --phase) PHASE="$2"; shift 2;; --apply) APPLY=1; shift;; --force) FORCE=1; shift;;
  --root) ROOT="$2"; shift 2;; --list) awk -F'\t' '!/^#/{c[$1]++} END{for(p in c) print "phase " p ": " c[p] " files"}' config/manifest.tsv | sort -V; exit 0;;
  *) echo "unknown arg $1"; exit 2;; esac; done
[ -n "$PHASE" ] || { echo "need --phase N (or --list)"; exit 2; }
n=0
while IFS=$'\t' read -r ph src dest mode; do
  [[ "$ph" == \#* || "$ph" != "$PHASE" ]] && continue
  t="$ROOT$dest"; n=$((n+1))
  if [ -e "$t" ] && [ "$FORCE" != 1 ]; then
    if cmp -s "$src" "$t"; then echo "same    $t"; else echo "DIFFERS $t (kept; --force to overwrite)"; fi; continue; fi
  if [ "$APPLY" != 1 ]; then echo "would write $t (mode $mode)"; continue; fi
  mkdir -p "$(dirname "$t")"
  [ -e "$t" ] && cp -p "$t" "$t.bak-$(date +%Y%m%d%H%M%S)"
  install -m "$mode" "$src" "$t"; echo "wrote   $t"
done < config/manifest.tsv
echo "phase $PHASE: $n files considered. $([ $APPLY = 1 ] || echo 'DRY RUN: add --apply to write.')"
