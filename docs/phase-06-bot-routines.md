# Phase 6: Per-bot daily routines

[Back to README](../README.md) | Previous: [phase-05-bots.md](phase-05-bots.md) | Next: [phase-07-cos-routine.md](phase-07-cos-routine.md)

## How the CoS runs this phase

1. Explain in 2-4 sentences what this phase does and why. Mark what is Grok-Bot-specific vs generic.
2. Show the relevant repo files/examples. Anything marked `EXAMPLE TEMPLATE, DO NOT USE AS-IS`: show as an example, ask the interview questions, draft the user's own version, get an explicit OK.
3. Ask before running anything that installs, overwrites, sends, spends, exposes a service, or is destructive.
4. Do the steps. Prefer `scripts/install.sh --phase 6` (dry run) then `--apply` for file copies.
5. Run the Verify section and report results honestly.
6. Record progress (PROGRESS.md + memory) and wait for the user before the next phase.

## Guide (from the operator's working system)

```text
Grok-Bot-specific: each bot creates its own routine with UpdateRoutine in its own main chat (subagents cannot change
routines or memory). Ask the bot: "create a daily routine at 3:14 AM <tz> with this prompt". Cron `14 3 * * *` or `14 3 * * 1-5`.
Each run: brief inbox + ack -> gather -> write report md -> items.json with stable keys -> brief publish -> post to chat with
dashboard link, ending with the ref-append Ref line. Silent only if nothing new AND nothing open.
Templates: examples/routines/templates.md. Real stored example: examples/routines/zero-gmail-triage.md. Other bots' exact routine
prompts were not available locally; build them from the template + the bot description.
Verify: "run your daily brief routine now" then `brief items --open --all`.
```

## Notes

Templates: [examples/routines/templates.md](../examples/routines/templates.md); real example: [examples/routines/zero-gmail-triage.md](../examples/routines/zero-gmail-triage.md).

## Verify

- Ask the bot: "run your daily brief routine now"; then `/home/box/bin/brief items --open --all | head` shows its items.
- The chat post ends with exactly one `Ref: <PREFIX>-<id>` line.

Done when the checks pass and the user agrees. Then update PROGRESS.md: `phase 6 done <date>; next 7`.
