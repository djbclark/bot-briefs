# Phase 14: Webhook dispatch (dashboard -> CoS, agent -> bot)

[Back to README](../README.md) | Previous: [phase-13-vps-mirror.md](phase-13-vps-mirror.md) | Next: [phase-15-rules.md](phase-15-rules.md)

## How the CoS runs this phase

1. Explain in 2-4 sentences what this phase does and why. Mark what is Grok-Bot-specific vs generic.
2. Show the relevant repo files/examples. Anything marked `EXAMPLE TEMPLATE, DO NOT USE AS-IS`: show as an example, ask the interview questions, draft the user's own version, get an explicit OK.
3. Ask before running anything that installs, overwrites, sends, spends, exposes a service, or is destructive.
4. Do the steps. Prefer `scripts/install.sh --phase 14` (dry run) then `--apply` for file copies.
5. Run the Verify section and report results honestly.
6. Record progress (PROGRESS.md + memory) and wait for the user before the next phase.

## Guide (from the operator's working system)

```text
Grok-Bot-specific: a WEBHOOK routine (UpdateRoutine, webhook trigger) gives a URL + key. CoS creates "Dashboard dispatch"; the
user stores AGENT_WEBHOOK_CHIEF_OF_STAFF_URL / _KEY in ~/.config/agent-webhook/secrets.env (600; template from install-shared-docs)
or as box env secrets. Never in chat, never committed.
Flow: Send / Submit all -> briefs/wake.py runs `notify-bot chief-of-staff -f dashboard -m none -s "Dashboard submit" "<N> actions submitted"`
-> CoS: `brief actions --status submitted --json` -> `brief mark ... processing` -> handle or route (SendToAgent) -> owner acks.
Unconfigured: dashboard shows "Processing trigger not configured yet"; bots pick actions up next run.
CoS webhook prompt (generic): "You were woken by the dashboard webhook. Run `brief actions --status submitted --json`, mark what you
take as processing, handle in rank order under the normal rules (never send anything without my approval card), route others to the
owning bot, ack each. Reply in chat only if something needs me."
Agents -> bots: Coding and Project Bot have webhook routines (examples/routines/coding-webhook.md, agent-webhook README).
Verify: `notify-bot chief-of-staff -n -s test "dry run"` (sends nothing); one real test only with the user's OK.
```

## Notes

Docs: [shared-docs/agent-webhook/README.md](../shared-docs/agent-webhook/README.md), messaging skill [SKILL.md](../shared-docs/agent-webhook/skill/grok-bot-messaging/SKILL.md), Coding webhook example [examples/routines/coding-webhook.md](../examples/routines/coding-webhook.md), registry [config/bots.json.example](../config/bots.json.example), secrets template [config/secrets.env.example](../config/secrets.env.example).

## Repo files and where they go (5 files)

Copy with `scripts/install.sh --phase 14` (dry run; add `--apply` to write; existing files are kept unless `--force`, which makes a `.bak-<timestamp>` first). Manual equivalent: `install -D -m <mode> <repo_path> <destination>`.

| repo path | destination | mode |
|---|---|---|
| [`config/bots.json.example`](../config/bots.json.example) | `/home/box/.config/agent-webhook/bots.json` | 644 |
| [`config/secrets.env.example`](../config/secrets.env.example) | `/home/box/.config/agent-webhook/secrets.env` | 600 |
| [`local-bin/notify-bot`](../local-bin/notify-bot) | `/home/box/.local/bin/notify-bot` | 755 |
| [`shared-docs/agent-webhook/README.md`](../shared-docs/agent-webhook/README.md) | `/home/box/agent-data/shared/agent-webhook/README.md` | 644 |
| [`shared-docs/agent-webhook/skill/grok-bot-messaging/SKILL.md`](../shared-docs/agent-webhook/skill/grok-bot-messaging/SKILL.md) | `/home/box/agent-data/shared/agent-webhook/skill/grok-bot-messaging/SKILL.md` | 644 |

## Verify

- `~/.local/bin/notify-bot --list` lists the bots; `~/.local/bin/notify-bot chief-of-staff -n -s test "dry run"` (`-n` = dry run, sends nothing).
- One real test only with the user's OK; then click Submit all on the dashboard and confirm the CoS wakes.

Done when the checks pass and the user agrees. Then update PROGRESS.md: `phase 14 done <date>; next 15`.
