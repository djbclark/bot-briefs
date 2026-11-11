#!/bin/bash
# Install the Saner MCP bridge as two LaunchAgents (macOS):
#   com.example.saner-mcp-bridge  mcp-proxy 127.0.0.1:18792 -> stdio saner_mcp.py
#   com.example.saner-mcp-auth    Caddy bearer gate 127.0.0.1:18793 -> 18792
# Then expose it. Prefer tailnet-only Serve:  tailscale serve --bg --https=8443 --set-path /<path> http://127.0.0.1:18793
# Use public Funnel only if a client truly cannot join the tailnet:  tailscale funnel --bg --https=8443 --set-path /<path> http://127.0.0.1:18793
# Prereqs: mcp-proxy (uv tool), uv, caddy, one Keychain item per client, <keychain-service>-<client> / <keychain-account>
#   (>= 32 random chars each; see auth-proxy.sh). Rotate them on a schedule.
# mcp-proxy has no auth of its own: keep it bound to 127.0.0.1 and turn on its origin/host
# checks (--allow-origin below; also any Host/DNS-rebinding option your version offers).
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
SERVER="${SANER_MCP_SERVER:-$HOME/.opencli/plugins/saner/mcp/saner_mcp.py}"
DEST="$HOME/.local/libexec/saner-mcp-bridge"
LOGS="$HOME/.hermes/logs"
PATHS="$HOME/.local/bin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
mkdir -p "$DEST" "$LOGS"
install -m 0644 "$SRC/Caddyfile" "$DEST/Caddyfile"
install -m 0755 "$SRC/auth-proxy.sh" "$SRC/smoke-test.sh" "$DEST/"

write_plist() { # label, log name, program args...
  local label=$1 log=$2; shift 2
  local plist="$HOME/Library/LaunchAgents/$label.plist" args=""
  for a in "$@"; do args+="		<string>$a</string>"$'\n'; done
  cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$label</string>
	<key>ProgramArguments</key>
	<array>
$args	</array>
	<key>EnvironmentVariables</key>
	<dict>
		<key>PATH</key>
		<string>$PATHS</string>
		<key>SANER_BRIDGE_OWNER</key>
		<string>saner-mcp</string>
	</dict>
	<key>RunAtLoad</key>
	<true/>
	<key>KeepAlive</key>
	<true/>
	<key>StandardOutPath</key>
	<string>$LOGS/$log.log</string>
	<key>StandardErrorPath</key>
	<string>$LOGS/$log.log</string>
</dict>
</plist>
EOF
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$plist"
  echo "loaded $label"
}

write_plist com.example.saner-mcp-bridge saner-mcp-bridge \
  "$HOME/.local/bin/mcp-proxy" --host 127.0.0.1 --port 18792 \
  --allow-origin "https://<mac>.<tailnet>.ts.net" --pass-environment -- \
  /opt/homebrew/bin/uv run --script "$SERVER"
write_plist com.example.saner-mcp-auth saner-mcp-auth "$DEST/auth-proxy.sh"
