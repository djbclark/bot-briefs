#!/bin/bash
# Usage: smoke-test.sh <url-ending-in-/mcp>   e.g. http://127.0.0.1:18793/mcp or the Funnel URL.
# Checks the bearer gate, initialize, tools/list, and a read-only saner_status call.
# The token is read from Keychain and never printed.
set -uo pipefail
URL=$1; CLIENT=${2:-grokbot}; T="$(security find-generic-password -s "<keychain-service>-$CLIENT" -a <keychain-account> -w)"
H=(-H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -H 'MCP-Protocol-Version: 2025-06-18')
AUTH() { printf 'Authorization: Bearer %s\n' "$T"; }
echo "no-token status: $(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" -X POST "$URL" -d '{}')"
echo "wrong-token status: $(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" -H 'Authorization: Bearer nope' -X POST "$URL" -d '{}')"
hdr=$(mktemp)
curl -s -D "$hdr" "${H[@]}" -H @<(AUTH) -X POST "$URL" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' | grep -o '"serverInfo":{[^}]*}'
SID=$(awk -F': ' 'tolower($1)=="mcp-session-id"{print $2}' "$hdr" | tr -d '\r')
S=(-H "X-Noop: 1"); [ -n "$SID" ] && S=(-H "Mcp-Session-Id: $SID")
curl -s "${H[@]}" "${S[@]}" -H @<(AUTH) -X POST "$URL" -d '{"jsonrpc":"2.0","method":"notifications/initialized"}' >/dev/null
echo "tools:"; curl -s "${H[@]}" "${S[@]}" -H @<(AUTH) -X POST "$URL" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | sed 's/^data: //' | grep '^{' | python3 -c 'import json,sys;[print("  ",t["name"]) for t in json.load(sys.stdin)["result"]["tools"]]'
echo "saner_status:"; curl -s --max-time 120 "${H[@]}" "${S[@]}" -H @<(AUTH) -X POST "$URL" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"saner_status","arguments":{}}}' | sed 's/^data: //' | grep '^{' | python3 -c '
import json,sys
r=json.load(sys.stdin)["result"]
print("   isError:", r.get("isError"), "|", r["content"][0]["text"][:300])'
rm -f "$hdr"
