#!/usr/bin/env bash
# secret-scan.sh [DIR] — scan a tree for common secrets and for YOUR personal terms.
# Personal deny-list: one term (or extended regex) per line, '#' comments allowed, read from
#   $SECRET_SCAN_DENYLIST (default ~/.config/secret-scan/denylist.txt). Keep that file OUTSIDE the repo
#   and never commit it; this script deliberately hardcodes no personal terms.
# Prints file:line:pattern-name only (never the matched value). Exit 1 if anything is found.
set -uo pipefail
DIR=${1:-.}
DENY=${SECRET_SCAN_DENYLIST:-$HOME/.config/secret-scan/denylist.txt}
EXCL=(--exclude-dir=.git --exclude-dir=node_modules --exclude-dir=.venv --exclude=secret-scan.sh)
hits=0
check() { # name regex [grep flags]
  local name=$1 re=$2; shift 2
  local out; out=$(grep -rnIE "$@" "${EXCL[@]}" -e "$re" "$DIR" 2>/dev/null | cut -d: -f1,2)
  [ -n "$out" ] && { hits=1; sed "s/\$/: $name/" <<<"$out"; }
}
check private-key '-----BEGIN [A-Z ]*PRIVATE KEY-----'
check aws-key 'AKIA[0-9A-Z]{16}'
check github-token 'gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}'
check slack-token 'xox[abprs]-[A-Za-z0-9-]{10,}'
check slack-webhook 'hooks\.slack\.com/services/[A-Za-z0-9/]{20,}'
check discord-webhook 'discord(app)?\.com/api/webhooks/[0-9]+/[A-Za-z0-9_-]{20,}'
check openai-anthropic-key 'sk-(ant-|proj-)?[A-Za-z0-9_-]{20,}'
check google-api-key 'AIza[0-9A-Za-z_-]{35}|ya29\.[0-9A-Za-z_-]{30,}'
check telegram-bot-token '[0-9]{8,10}:AA[0-9A-Za-z_-]{30,}'
check tailscale-key 'tskey-[A-Za-z0-9-]{10,}'
check bearer-literal '[Bb]earer[[:space:]]+[A-Za-z0-9._~+/=-]{24,}'
check jwt 'eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}'
# public IPv4 (skips loopback, RFC1918, CGNAT/Tailscale 100.64/10, 198.18/15, 0.x, and version-like x.y.z.w in paths)
ipout=$(grep -rnIoE "${EXCL[@]}" '\b([0-9]{1,3}\.){3}[0-9]{1,3}\b' "$DIR" 2>/dev/null | awk -F: '{
  split($3,o,"."); a=o[1]+0; b=o[2]+0; bad=0; for(i=1;i<=4;i++) if(o[i]+0>255) bad=1
  if (bad||a==0||a==127||a==10||a>=224||(a==192&&b==168)||(a==172&&b>=16&&b<=31)||(a==100&&b>=64&&b<=127)||(a==169&&b==254)||(a==198&&(b==18||b==19))) next
  print $1":"$2": public-ipv4"}' | sort -u)
[ -n "$ipout" ] && { hits=1; echo "$ipout"; }
if [ -r "$DENY" ]; then
  n=0
  while IFS= read -r term || [ -n "$term" ]; do
    term="${term%%#*}"; term="${term%"${term##*[![:space:]]}"}"; [ -z "$term" ] && continue
    n=$((n + 1)); check "denylist-term-$n" "$term" -i
  done < "$DENY"
  echo "secret-scan: checked $n personal deny-list terms from $DENY" >&2
else
  echo "secret-scan: no deny-list at $DENY (create it outside the repo to check personal terms)" >&2
fi
[ $hits = 0 ] && echo "secret-scan: clean" >&2
exit $hits
