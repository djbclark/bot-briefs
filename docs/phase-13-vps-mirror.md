# Phase 13: VPS mirror of the dashboard (optional)

[Back to README](../README.md) | Previous: [phase-12-backups.md](phase-12-backups.md) | Next: [phase-14-webhook-dispatch.md](phase-14-webhook-dispatch.md)

## How the CoS runs this phase

1. Explain in 2-4 sentences what this phase does and why. Mark what is Grok-Bot-specific vs generic.
2. Show the relevant repo files/examples. Anything marked `EXAMPLE TEMPLATE, DO NOT USE AS-IS`: show as an example, ask the interview questions, draft the user's own version, get an explicit OK.
3. Ask before running anything that installs, overwrites, sends, spends, exposes a service, or is destructive.
4. Do the steps. Prefer `scripts/install.sh --phase 13` (dry run) then `--apply` for file copies.
5. Run the Verify section and report results honestly.
6. Record progress (PROGRESS.md + memory) and wait for the user before the next phase.

## Guide (from the operator's working system)

```text
Second copy on an always-on VPS in the tailnet, readable/actionable when the box is frozen. Box authoritative.
Design + test log: examples/reports/vps-mirror.md; build prompt: examples/handoffs/claude-vps-build.md; code: briefs/mirror.py,
briefs/syncapi.py, ops/vps/*, README section 10.
 * Box->VPS: `python -m briefs.mirror run` (monit briefs-mirror) diffs row hashes every 10 s, POSTs /sync/push via SOCKS; heartbeat; mass-delete guard.
 * VPS->box: VPS actions get ids >= 1,000,000,000; box polls /sync/actions every 10 s, inserts once (mirror_vps_actions), acks.
 * Wake: VPS Send/Submit runs ops/vps/notify-bot-vps -> same CoS webhook, spool + retry timer.
 * Auth: identity headers as on the box; /sync/* needs `Authorization: Bearer <sync token>`. Create:
   `openssl rand -hex 32 > ~/.config/briefs-mirror/token; chmod 600`; copy via scp to /var/lib/briefs/secrets/sync-token (600);
   webhook settings in /var/lib/briefs/secrets/webhook.env (600).
 * VPS units: ops/vps/briefs-web.service, briefs-wake-retry.{service,timer}, briefs-backup.{service,timer} (e.g. systemd user
   units of root with linger on NixOS; system units fine elsewhere). uv venv; Serve --https=443 tailnet-only.
 * Health: red banner if no update > 30 min; /healthz/mirror; box shared/briefs-mirror/status.json + lag.log.
Steps: provision, join tailnet, ops/vps/pack-app, install units, set BRIEFS_SYNC_TOKEN_FILE, backfill, start mirror,
`python -m briefs.mirror verify`.  Verify: `scripts/verify.sh 13`.
```

## Notes

Reference: [examples/reports/vps-mirror.md](../examples/reports/vps-mirror.md) and [examples/handoffs/claude-vps-build.md](../examples/handoffs/claude-vps-build.md). Code: `dashboard/briefs/mirror.py`, `dashboard/briefs/syncapi.py`, `dashboard/ops/vps/`. Skip unless the user has an always-on VPS in the tailnet.

## Verify

```bash
scripts/verify.sh 13
```

Done when the checks pass and the user agrees. Then update PROGRESS.md: `phase 13 done <date>; next 14`.
