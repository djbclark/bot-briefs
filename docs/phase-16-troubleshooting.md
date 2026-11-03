# Phase 16: Verification and troubleshooting

[Back to README](../README.md) | Previous: [phase-15-rules.md](phase-15-rules.md) | Next: [phase-17-sync-to-public.md](phase-17-sync-to-public.md)

## How the CoS runs this phase

1. Explain in 2-4 sentences what this phase does and why. Mark what is Grok-Bot-specific vs generic.
2. Show the relevant repo files/examples. Anything marked `EXAMPLE TEMPLATE, DO NOT USE AS-IS`: show as an example, ask the interview questions, draft the user's own version, get an explicit OK.
3. Ask before running anything that installs, overwrites, sends, spends, exposes a service, or is destructive.
4. Do the steps. Prefer `scripts/install.sh --phase 16` (dry run) then `--apply` for file copies.
5. Run the Verify section and report results honestly.
6. Record progress (PROGRESS.md + memory) and wait for the user before the next phase.

## Guide (from the operator's working system)

```text
 * Box freezes/pauses (67 s - 15 min seen): tailscaled may lose control; wd-ts-health restarts it. Logs:
   ~/.local/state/watchdogs/watchdog.log, ~/.local/share/tailscale/tailscaled.log ("time jump detected", "long-poll timed out").
 * URL down but local /healthz ok: `tailscale serve status`; wd-dashboard-url re-applies after 60 s, alerts after 20 min. Probe via SOCKS.
 * Dashboard wedged: monit restarts after 2 failed cycles; `monit -c ~/.config/monit/monitrc restart dashboard`.
 * "database is locked": retry; never run two importers.
 * Auto-review "An error occured while classifying this action": agy fallback (phase 7).
 * opencli not signed in: sign in on the box desktop Chrome; wd-saner pings once.
 * Mac fallback unreachable: normal when asleep; keep the computer connector app running.
 * notify-bot 401/404: wrong URL/key; `notify-bot --list`, `-n`.
 * monit gone after container restart: open a shell (bashrc ensure) or run watchdog-ensure.
 * Known test failure: tests/test_m2.py::test_snooze_hides_then_reappears uses a hard-coded past date.
```

## Notes

Also see [grok-bot-constraints.md](grok-bot-constraints.md).

## Verify

```bash
scripts/verify.sh all
```

Done when the checks pass and the user agrees. Then update PROGRESS.md: `phase 16 done <date>; next 16`.
