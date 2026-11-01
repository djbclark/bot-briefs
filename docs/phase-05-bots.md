# Phase 5: Create the specialist bots

[Back to README](../README.md) | Previous: [phase-04-helpers.md](phase-04-helpers.md) | Next: [phase-06-bot-routines.md](phase-06-bot-routines.md)

## How the CoS runs this phase

1. Explain in 2-4 sentences what this phase does and why. Mark what is Grok-Bot-specific vs generic.
2. Show the relevant repo files/examples. Anything marked `EXAMPLE FROM DANIEL, DO NOT USE AS-IS`: show as an example, ask the interview questions, draft the user's own version, get an explicit OK.
3. Ask before running anything that installs, overwrites, sends, spends, exposes a service, or is destructive.
4. Do the steps. Prefer `scripts/install.sh --phase 5` (dry run) then `--apply` for file copies.
5. Run the Verify section and report results honestly.
6. Record progress (PROGRESS.md + memory) and wait for the user before the next phase.

## Guide (from Daniel's working system)

```text
Grok-Bot-specific: CreateAgent tool (or app UI: new bot -> name, description, avatar). The description is the bot's
standing instructions; each bot has its own chat, memory, routines; all share the box.
Daniel's fleet (examples/bots/<slug>.md, EXAMPLE FROM DANIEL, DO NOT USE AS-IS):
  chief-of-staff (manages bots, daily list, dispatch) | radar (work: GitHub, Jira, work mail; security watch) | zero (Gmail
  inbox zero 2x/day; never send/delete) | family | money (purchases, subscriptions) | business (small-business ops) | chess (club)
  | pika (housing co-op) | friends | techno-euphora (side project + repos) | one-offs | coding (agent tooling; webhook) | other (catch-all TODOs)
Pattern ("desk of one"): Mission / Scope (OWN vs NOT) / Sources (read-only) / Schedule / Rules (watch, draft, remind) /
Dashboard (slug, Ref prefix) / Escalation to CoS.
Per bot: show example, ask questions, draft, OK, CreateAgent. Record the agent id in briefs/config.py BOTS, bots.json
(webhook bots only) and the CoS prompt. Start with 3-5 bots.
```

## Notes

Examples: [examples/bots/](../examples/bots/README.md) (one file per bot, each with interview questions).

## Verify

- Ask each new bot "what is your mission, your dashboard slug and your Ref prefix?"; answers must match the approved description.
- The agent id of every bot is recorded in `briefs/config.py` BOTS (and `~/.config/agent-webhook/bots.json` for webhook bots).

Done when the checks pass and the user agrees. Then update PROGRESS.md: `phase 5 done <date>; next 6`.
