# Phase 10: Networking: Tailscale userspace + Serve (never Funnel)

[Back to README](../README.md) | Previous: [phase-09-ai-agents.md](phase-09-ai-agents.md) | Next: [phase-11-watchdogs.md](phase-11-watchdogs.md)

## How the CoS runs this phase

1. Explain in 2-4 sentences what this phase does and why. Mark what is Grok-Bot-specific vs generic.
2. Show the relevant repo files/examples. Anything marked `EXAMPLE FROM DANIEL, DO NOT USE AS-IS`: show as an example, ask the interview questions, draft the user's own version, get an explicit OK.
3. Ask before running anything that installs, overwrites, sends, spends, exposes a service, or is destructive.
4. Do the steps. Prefer `scripts/install.sh --phase 10` (dry run) then `--apply` for file copies.
5. Run the Verify section and report results honestly.
6. Record progress (PROGRESS.md + memory) and wait for the user before the next phase.

## Guide (from Daniel's working system)

```text
The box has no /dev/net/tun, so tailscaled runs in USERSPACE mode (scripts/start-tailscaled.sh):
  tailscaled --tun=userspace-networking --socks5-server=localhost:1055 --outbound-http-proxy-listen=localhost:1056 \
    --statedir=~/.local/share/tailscale --socket=~/.local/share/tailscale/tailscaled.sock
Join: `tailscale --socket=<sock> up --hostname=<box-name>` (login URL), or an auth key the user creates at
https://login.tailscale.com/admin/settings/keys stored as box secret TS_AUTHKEY (`--auth-key=$TS_AUTHKEY`).
Enable MagicDNS + HTTPS certificates in the admin console, then (scripts/serve-dashboard.sh):
  tailscale --socket=<sock> serve --bg --https=443 http://127.0.0.1:8642
URL: https://<box-name>.<tailnet>.ts.net/ (Daniel: grok-bot-box.<his-tailnet>.ts.net). NEVER `tailscale funnel`.
SOCKS probe quirk: in userspace mode the box cannot reach tailnet addresses directly; every probe from the box (even of its
own URL) must use the SOCKS proxy: `curl --socks5-hostname localhost:1055 https://<box>.<tailnet>.ts.net/healthz` -> ok.
ssh to tailnet hosts: `ProxyCommand ncat --proxy 127.0.0.1:1055 --proxy-type socks5 %h %p` (config/ssh_config.example).
Time-jump issue: after box pauses tailscaled may keep a dead control connection ("map response long-poll timed out");
wd-ts-health restarts it (75 s with that signature, else 4 min; optional `sudo -n ss -K` first). Daniel runs a patched
tailscaled (upstream PR #21834 + TS_DROP_NOISE_ON_TIME_JUMP=1); stock + watchdog is fine.  Verify: `scripts/verify.sh 10 https://<box>.<tailnet>.ts.net`.
```

## Notes

SSH structure example: [config/ssh_config.example](../config/ssh_config.example). Ask before every network change; Funnel is forbidden for the dashboard.

## Verify

```bash
scripts/verify.sh 10 https://<box>.<tailnet>.ts.net
```

Done when the checks pass and the user agrees. Then update PROGRESS.md: `phase 10 done <date>; next 11`.
