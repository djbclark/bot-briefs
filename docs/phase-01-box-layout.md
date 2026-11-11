# Phase 1: Box / shell layout

[Back to README](../README.md) | Previous: [phase-00-prereqs.md](phase-00-prereqs.md) | Next: [phase-02-dashboard.md](phase-02-dashboard.md)

## How the CoS runs this phase

1. Explain in 2-4 sentences what this phase does and why. Mark what is Grok-Bot-specific vs generic.
2. Show the relevant repo files/examples. Anything marked `EXAMPLE TEMPLATE, DO NOT USE AS-IS`: show as an example, ask the interview questions, draft the user's own version, get an explicit OK.
3. Ask before running anything that installs, overwrites, sends, spends, exposes a service, or is destructive.
4. Do the steps. Prefer `scripts/install.sh --phase 1` (dry run) then `--apply` for file copies.
5. Run the Verify section and report results honestly.
6. Record progress (PROGRESS.md + memory) and wait for the user before the next phase.

## Guide (from the operator's working system)

```text
  /home/box/bin/                 shared scripts every bot calls by full path (brief, ref, ref-append, report-to-items, cos-gather)
  /home/box/.local/bin/          tools (acp-run, notify-bot, restic, rclone, tailscale(d), opencli, agents)
  /home/box/agent-data/shared/   readable by ALL bots: docs (bot-dashboard.md...), dashboard/, refs/, watchdogs/, agent-webhook/,
                                 source-health.json, briefs-mirror/
  /home/box/src/                 checkouts (one-offs/cos-gather, project repos)
  /workspace/                    per-run scratch: report-YYYYMMDD.md, items-YYYYMMDD.json, cos-gather.json
  ~/.local/state/watchdogs/      watchdog logs/state ;  ~/.config/monit/monitrc ;  ~/.config/agent-webhook/ (bots.json + secrets.env 600)
PATH: ~/.local/bin and /home/box/bin on PATH in ~/.bashrc and ~/.profile.
NO systemd and NO cron on the Grok Bot box: ~/.bashrc and ~/.profile call ~/.local/bin/dashboard-ensure (shim for
watchdog-ensure) so the first shell after a container restart brings monit back (added in phase 11).
Run scripts/install-deps.sh (review first: apt + curl installers), scripts/install-dirs.sh.  Verify: `scripts/verify.sh 1`.
```

## Notes

Scripts: [scripts/install-deps.sh](../scripts/install-deps.sh) (apt + curl installers: review with the user first), [scripts/install-dirs.sh](../scripts/install-dirs.sh).

## Verify

```bash
scripts/verify.sh 1
```

Done when the checks pass and the user agrees. Then update PROGRESS.md: `phase 1 done <date>; next 2`.
