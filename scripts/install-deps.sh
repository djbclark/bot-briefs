#!/usr/bin/env bash
# Phase 1: OS packages + user tools. Review before running; safe to re-run.
set -euo pipefail
if command -v sudo >/dev/null && sudo -n true 2>/dev/null; then
  sudo apt-get update -y
  sudo apt-get install -y monit sqlite3 python3 python3-venv python3-pip git curl jq ncat rsync ca-certificates unzip bzip2 || true
else
  echo "no passwordless sudo: install monit sqlite3 python3-venv git curl jq ncat yourself"
fi
mkdir -p ~/.local/bin; export PATH="$HOME/.local/bin:$PATH"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
command -v restic >/dev/null || { V=0.18.1; curl -fsSL -o /tmp/restic.bz2 "https://github.com/restic/restic/releases/download/v$V/restic_${V}_linux_amd64.bz2" && bunzip2 -f /tmp/restic.bz2 && install -m 755 /tmp/restic ~/.local/bin/restic; } || echo "restic: install manually from https://restic.net (pick the latest version)"
command -v rclone >/dev/null || { curl -fsSL -o /tmp/rclone.zip https://downloads.rclone.org/rclone-current-linux-amd64.zip && rm -rf /tmp/rclone && unzip -q /tmp/rclone.zip -d /tmp/rclone && install -m 755 /tmp/rclone/rclone-*/rclone ~/.local/bin/rclone; } || echo "rclone: install manually"
command -v tailscaled >/dev/null || { curl -fsSL -o /tmp/ts.tgz https://pkgs.tailscale.com/stable/tailscale_latest_amd64.tgz && tar -xzf /tmp/ts.tgz -C /tmp && install -m 755 /tmp/tailscale_*_amd64/tailscale /tmp/tailscale_*_amd64/tailscaled ~/.local/bin/; } || echo "tailscale: see https://pkgs.tailscale.com/stable/#static"
echo "Saner/Shortwave only: install Node 22, then: npm i -g --prefix ~/.local @jackwener/opencli"
grep -q 'agent-kit PATH' ~/.bashrc 2>/dev/null || echo 'export PATH="$HOME/.local/bin:/home/box/bin:$PATH" # agent-kit PATH' >> ~/.bashrc
grep -q 'agent-kit PATH' ~/.profile 2>/dev/null || echo 'export PATH="$HOME/.local/bin:/home/box/bin:$PATH" # agent-kit PATH' >> ~/.profile
echo done
