# Phase 3: The `brief` CLI and the bot contract

[Back to README](../README.md) | Previous: [phase-02-dashboard.md](phase-02-dashboard.md) | Next: [phase-04-helpers.md](phase-04-helpers.md)

## How the CoS runs this phase

1. Explain in 2-4 sentences what this phase does and why. Mark what is Grok-Bot-specific vs generic.
2. Show the relevant repo files/examples. Anything marked `EXAMPLE TEMPLATE, DO NOT USE AS-IS`: show as an example, ask the interview questions, draft the user's own version, get an explicit OK.
3. Ask before running anything that installs, overwrites, sends, spends, exposes a service, or is destructive.
4. Do the steps. Prefer `scripts/install.sh --phase 3` (dry run) then `--apply` for file copies.
5. Run the Verify section and report results honestly.
6. Record progress (PROGRESS.md + memory) and wait for the user before the next phase.

## Guide (from the operator's working system)

```text
/home/box/bin/brief wraps `dashboard/.venv/bin/python -m briefs.cli`. Writes SQLite directly (no HTTP, no credentials).
  brief init
  brief publish --bot <slug> --kind daily|weekly --date YYYY-MM-DD --title "..." --md report.md --items-json items.json
        (re-publishing bot+date+kind updates in place; prints JSON with report_id, items, url)
  brief inbox --bot <slug> --json [--include-seen] [--peek]
  brief ack <id>... --status applied|discuss|failed --note "one line" [--keep-open]
  brief mark <id>... processing ; brief actions --status submitted --json [--bot x] ; brief items --open [--all] [--json] ; brief search "x"
Stable keys (best first): gmail:<threadId>, issue/PR URL, gcal:<eventId>, reused slug like money:power-bill-oct.
items.json: [{"id":"3","item":"School form","details":"Sign + return","links":["https://..."],"key":"family:school-form","due":"2026-10-30"}]
Steps: the files listed under 'Repo files' below (`scripts/install.sh --phase N`), the files listed under 'Repo files' below (`scripts/install.sh --phase N`); replace the operator's URL/bot table in bot-dashboard.md; publish a tiny
test report and look at it.  Verify: `scripts/verify.sh 3`.
NOTE: Google reply sheets are RETIRED (2026-10-27); bot-report-sheets.md is history only. Some sheet wording in
bot-dashboard.md is legacy; --items-json is current.
```

## Notes

The bot integration contract every bot follows is [shared-docs/bot-dashboard.md](../shared-docs/bot-dashboard.md) (replace the operator's URL and bot table). [shared-docs/bot-report-sheets.md](../shared-docs/bot-report-sheets.md) is RETIRED history.

## Repo files and where they go (6 files)

Copy with `scripts/install.sh --phase 3` (dry run; add `--apply` to write; existing files are kept unless `--force`, which makes a `.bak-<timestamp>` first). Manual equivalent: `install -D -m <mode> <repo_path> <destination>`.

| repo path | destination | mode |
|---|---|---|
| [`bin/brief`](../bin/brief) | `/home/box/bin/brief` | 755 |
| [`shared-docs/bot-dashboard.md`](../shared-docs/bot-dashboard.md) | `/home/box/agent-data/shared/bot-dashboard.md` | 644 |
| [`shared-docs/bot-report-sheets.md`](../shared-docs/bot-report-sheets.md) | `/home/box/agent-data/shared/bot-report-sheets.md` | 644 |
| [`shared-docs/coding-bot-setup.md`](../shared-docs/coding-bot-setup.md) | `/home/box/agent-data/shared/coding-bot-setup.md` | 644 |
| [`shared-docs/restic-backup.md`](../shared-docs/restic-backup.md) | `/home/box/agent-data/shared/restic-backup.md` | 644 |
| [`shared-docs/watchdogs.md`](../shared-docs/watchdogs.md) | `/home/box/agent-data/shared/watchdogs.md` | 644 |

## Verify

```bash
scripts/verify.sh 3
```

Done when the checks pass and the user agrees. Then update PROGRESS.md: `phase 3 done <date>; next 4`.
