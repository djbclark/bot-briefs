#!/usr/bin/env bash
# Phase 10: expose the dashboard to the TAILNET ONLY. Never use `tailscale funnel`.
set -euo pipefail
tailscale --socket=/home/box/.local/share/tailscale/tailscaled.sock serve --bg --https=443 http://127.0.0.1:8642
