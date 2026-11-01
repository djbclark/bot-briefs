#!/bin/bash
# Reads one bearer token PER CLIENT from Keychain (never on disk) and runs the Caddy gate.
#
# Security model (see SECURITY.md):
#   - One token per client (e.g. "grokbot", "laptop"). Never share a token between
#     services, so a leak can be revoked without breaking every client.
#   - Rotate each token on a schedule (e.g. every 90 days) and on any suspected leak;
#     a rotation takes effect on restart: launchctl kickstart -k gui/$(id -u)/<launchd-label>
#   - Prefer tailnet-only `tailscale serve` over public Funnel whenever the client can
#     reach the tailnet.
# Keychain items: service "<keychain-service>-<client>", account "<keychain-account>".
set -euo pipefail
CLIENTS=(grokbot laptop)   # must match the matchers in the Caddyfile
for c in "${CLIENTS[@]}"; do
  v="$(security find-generic-password -s "<keychain-service>-$c" -a "<keychain-account>" -w 2>/dev/null || true)"
  if [ ${#v} -lt 32 ]; then
    # Never leave a token empty: "Bearer " with no token would then match.
    # A random throwaway value disables this client instead.
    echo "token for client '$c' missing or too short; client disabled" >&2
    v="$(openssl rand -hex 32)"
  fi
  export "MCP_TOKEN_$(tr a-z A-Z <<<"$c")=$v"
done
exec /opt/homebrew/bin/caddy run --adapter caddyfile --config "$(dirname "$0")/Caddyfile"
