#!/usr/bin/env bash
# Phase 10: tailscaled in userspace mode. Then run `tailscale up` yourself.
D=/home/box/.local/share/tailscale; mkdir -p $D
pgrep -f "tailscaled .*--socket=$D/tailscaled.sock" >/dev/null && { echo already running; exit 0; }
TS_DROP_NOISE_ON_TIME_JUMP=1 setsid nohup tailscaled --tun=userspace-networking --socks5-server=localhost:1055 --outbound-http-proxy-listen=localhost:1056 --statedir=$D --socket=$D/tailscaled.sock >> $D/tailscaled.log 2>&1 < /dev/null &
sleep 3; echo "next: tailscale --socket=$D/tailscaled.sock up --hostname=<your-box-name>"
