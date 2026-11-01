# Phase 11: Watchdogs (monit)

[Back to README](../README.md) | Previous: [phase-10-networking.md](phase-10-networking.md) | Next: [phase-12-backups.md](phase-12-backups.md)

## How the CoS runs this phase

1. Explain in 2-4 sentences what this phase does and why. Mark what is Grok-Bot-specific vs generic.
2. Show the relevant repo files/examples. Anything marked `EXAMPLE FROM DANIEL, DO NOT USE AS-IS`: show as an example, ask the interview questions, draft the user's own version, get an explicit OK.
3. Ask before running anything that installs, overwrites, sends, spends, exposes a service, or is destructive.
4. Do the steps. Prefer `scripts/install.sh --phase 11` (dry run) then `--apply` for file copies.
5. Run the Verify section and report results honestly.
6. Record progress (PROGRESS.md + memory) and wait for the user before the next phase.

## Guide (from Daniel's working system)

```text
monit: apt-packaged, runs unprivileged as user box, real HTTP/TCP/program checks. Layout (the files listed under 'Repo files' below (`scripts/install.sh --phase N`), doc shared/watchdogs.md):
  ~/.config/monit/monitrc (0600): daemon 30 s, unix-socket httpd, include shared/watchdogs/conf.d/*.monitrc
  conf.d/services.monitrc: tailscaled, tailscale-health, mac-mcp-forwarder, dashboard (/healthz), dashboard-url, dashboard-tasks,
    beeper-server, risk-watch, guest-sshd, cline-hub, guardian, saner ; briefs-mirror.monitrc ; hermes.monitrc
  bin/wd-svc start|stop|restart NAME (backoff 30 s*2^(n-1) to 15 min), wd-ts-health, wd-dashboard-url (SOCKS probe, re-apply
    Serve after 60 s, alert after 20 min), wd-dashboard-tasks (re-apply Serve, nightly import, log trim), watchdog-guardian
    (restarts monit), watchdog-ensure (startup hook).
PERSONALIZE: remove checks for services the user doesn't run; replace hermes-ping. If you add a monit httpd password, make your own.
Steps: monit via scripts/install-deps.sh; the files listed under 'Repo files' below (`scripts/install.sh --phase N`); edit conf.d; `monit -c ~/.config/monit/monitrc -t`; scripts/start-watchdogs.sh;
add to ~/.bashrc and ~/.profile: `[ -x ~/.local/bin/dashboard-ensure ] && ~/.local/bin/dashboard-ensure >/dev/null 2>&1`
Verify: `scripts/verify.sh 11`; kill the dashboard and see it return within ~1 min.
```

## Notes

Doc: [shared-docs/watchdogs.md](../shared-docs/watchdogs.md). Before starting monit, delete checks for services the user does not run from `watchdogs/conf.d/services.monitrc` and replace `hermes-ping` (alert channel) with theirs or a no-op.

## Repo files and where they go (13 files)

Copy with `scripts/install.sh --phase 11` (dry run; add `--apply` to write; existing files are kept unless `--force`, which makes a `.bak-<timestamp>` first). Manual equivalent: `install -D -m <mode> <repo_path> <destination>`.

| repo path | destination | mode |
|---|---|---|
| [`watchdogs/bin/eve-drop-watch`](../watchdogs/bin/eve-drop-watch) | `/home/box/agent-data/shared/watchdogs/bin/eve-drop-watch` | 755 |
| [`watchdogs/bin/watchdog-ensure`](../watchdogs/bin/watchdog-ensure) | `/home/box/agent-data/shared/watchdogs/bin/watchdog-ensure` | 755 |
| [`watchdogs/bin/watchdog-guardian`](../watchdogs/bin/watchdog-guardian) | `/home/box/agent-data/shared/watchdogs/bin/watchdog-guardian` | 755 |
| [`watchdogs/bin/wd-dashboard-tasks`](../watchdogs/bin/wd-dashboard-tasks) | `/home/box/agent-data/shared/watchdogs/bin/wd-dashboard-tasks` | 755 |
| [`watchdogs/bin/wd-dashboard-url`](../watchdogs/bin/wd-dashboard-url) | `/home/box/agent-data/shared/watchdogs/bin/wd-dashboard-url` | 755 |
| [`watchdogs/bin/wd-hermes`](../watchdogs/bin/wd-hermes) | `/home/box/agent-data/shared/watchdogs/bin/wd-hermes` | 755 |
| [`watchdogs/bin/wd-saner`](../watchdogs/bin/wd-saner) | `/home/box/agent-data/shared/watchdogs/bin/wd-saner` | 755 |
| [`watchdogs/bin/wd-svc`](../watchdogs/bin/wd-svc) | `/home/box/agent-data/shared/watchdogs/bin/wd-svc` | 755 |
| [`watchdogs/bin/wd-ts-health`](../watchdogs/bin/wd-ts-health) | `/home/box/agent-data/shared/watchdogs/bin/wd-ts-health` | 755 |
| [`watchdogs/conf.d/briefs-mirror.monitrc`](../watchdogs/conf.d/briefs-mirror.monitrc) | `/home/box/agent-data/shared/watchdogs/conf.d/briefs-mirror.monitrc` | 644 |
| [`watchdogs/conf.d/hermes.monitrc`](../watchdogs/conf.d/hermes.monitrc) | `/home/box/agent-data/shared/watchdogs/conf.d/hermes.monitrc` | 644 |
| [`watchdogs/conf.d/services.monitrc`](../watchdogs/conf.d/services.monitrc) | `/home/box/agent-data/shared/watchdogs/conf.d/services.monitrc` | 644 |
| [`watchdogs/monitrc`](../watchdogs/monitrc) | `/home/box/.config/monit/monitrc` | 600 |

## Verify

```bash
scripts/verify.sh 11
```

Done when the checks pass and the user agrees. Then update PROGRESS.md: `phase 11 done <date>; next 12`.
