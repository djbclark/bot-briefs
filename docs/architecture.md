# Architecture

[Back to README](../README.md)

## One paragraph

A fleet of single-purpose Grok Bot bots ("desks": in Daniel's case Radar = work, Zero = inbox zero, Family, Money, Business, Chess club, Pika co-op, Friends, Techno Euphora side project, One-offs, Coding, Other = catch-all TODOs) each run a daily routine at ~3:14 AM, write a markdown report and publish it with the `brief` CLI into a local SQLite DB served by a small FastAPI + htmx app (the "Bot Briefs dashboard") on 127.0.0.1:8642. The box joins the user's tailnet with tailscaled in userspace mode and exposes the app ONLY to the tailnet with `tailscale serve` (identity headers gate it to the owner's login). At ~6:16 AM the Chief of Staff runs `cos-gather` (stdlib script reading every bot's latest report + Saner/Shortwave TODOs), ranks the pool, posts ONE high-priority list in chat and publishes it to the dashboard. The user acts with dashboard buttons (done / dismiss / snooze / priority / reply, Send / Submit all). Submitted actions wake the CoS through a webhook routine (`notify-bot chief-of-staff`), which routes them; every bot also reads its `brief inbox` at the start of each run and `brief ack`s. Every post ends with a unique `Ref: BOT-xx` line from a shared base-36 counter. monit keeps services alive (no systemd/cron on the box), restic + rclone back up to Google Drive nightly, an optional VPS mirrors the dashboard, and heavy coding goes to other agents (agy, Claude Code, Cline, Cursor, Hermes) via `acp-run`.

## Diagram

```text
 Gmail/Calendar/Drive/Slack/Jira/GitHub/Beeper/X  (Grok Bot connectors, OAuth)
 Saner.ai / Shortwave (opencli in box Chrome)      Mac fallback (computer connector, Hermes)
                 |                                         |
                 v                                         v
  +-------------------- specialist bots (one chat + memory + routines each) -----------+
  | radar  zero  family  money  business  chess  pika  friends  techno-euphora  ...    |
  |   03:14 routine: brief inbox/ack -> gather -> report.md + items.json -> publish   |
  +---------------------------------------|-------------------------------------------+
                                          v   /home/box/bin/brief (writes SQLite directly)
   /home/box/agent-data/shared/dashboard  [FastAPI + SQLite FTS5 + htmx, 127.0.0.1:8642]
        ^ cos-gather (read-only)          |  tailscale serve --https=443 (tailnet only, NEVER funnel)
        |                                 v
  Chief of Staff 06:16: rank -> chat post + `cos` report      user's browser on the tailnet
        ^                                                          | Send / Submit all
        +---- webhook routine <--- notify-bot <--- briefs/wake.py <+
  monit (watchdogs) keeps tailscaled / dashboard / Serve / helpers alive; restic+rclone -> Drive nightly
  optional: VPS mirror (briefs/mirror.py <-> /sync/*), acp-run -> Claude Code / agy / Cline / Cursor / Hermes
```

## Daily rhythm (Daniel's, America/New_York; EXAMPLE, ask the user for theirs)

| time | what |
|---|---|
| 03:14 | every bot's brief routine (weekday-only bots skip weekends; Friday weekly reviews) -> `brief publish`; nightly restic backup |
| 03:30 / ~03:45 | VPS DB backup / dashboard safety-net import |
| 04:58-05:10 | home network reboot window: watchdogs skip actions |
| 06:16 | Chief of Staff high-priority list (chat + dashboard report `cos`) |
| 08:00 / 16:00 | Zero's Gmail triage (weekdays) |
| any time | dashboard Send / Submit all -> CoS dispatch webhook routine |

## Box layout

| path | purpose |
|---|---|
| `/home/box/bin/` | shared scripts every bot calls by full path (repo `bin/`) |
| `/home/box/.local/bin/` | tools and helpers (repo `local-bin/`) |
| `/home/box/agent-data/shared/` | readable by ALL bots: docs (repo `shared-docs/`), `dashboard/` (repo `dashboard/`), `refs/`, `watchdogs/` (repo `watchdogs/`), `agent-webhook/`, `source-health.json`, `briefs-mirror/` |
| `/home/box/.opencli/plugins/` | opencli plugins (repo `opencli-plugins/`) |
| `~/.config/monit/monitrc`, `~/.config/agent-webhook/` | monit config; notify-bot registry + secrets (600) (repo `config/`) |
| `/workspace/` | per-run scratch (reports, items json, cos-gather.json) |

Exact copy map: [config/manifest.tsv](../config/manifest.tsv) (phase, repo path, destination, mode).

## Generic vs Grok-Bot-specific

See [grok-bot-constraints.md](grok-bot-constraints.md). Short version: bots, routines, connectors, SendToAgent, ReadTranscript, approval cards and box secrets are product features; everything in `bin/`, `local-bin/`, `dashboard/`, `watchdogs/`, `opencli-plugins/` is ordinary Linux software.
