# Bot Briefs system: executive summary

The system turns a set of single-purpose Grok Bot assistants into one daily briefing that the owner can act on. About a dozen specialist bots each cover one area of life: work radar, inbox zero, family, money, a small business, a chess club, a housing co-op, friends, a side project, coding, one-off requests and a catch-all for outside TODO apps. At about 3:14 AM each bot reads its connected sources (Gmail, Calendar, GitHub, Jira, Slack, Beeper chats, Saner.ai, Shortwave), writes a short markdown report and publishes it with a small `brief` command-line tool.

The reports go into a private dashboard, "Bot Briefs": a FastAPI and SQLite web app on the shared Grok Bot box. It is reachable only from the owner's Tailscale network, through Tailscale Serve and an identity check; it never uses Funnel. At 6:16 AM the Chief of Staff bot gathers every report and outside TODO, ranks the whole pool, and posts one high-priority list. The owner works from the list or from dashboard buttons such as done, dismiss, snooze, priority and reply. When the owner presses Send, a webhook wakes the Chief of Staff, which routes each action to the right bot. Bots also check their dashboard inbox at the start of every run.

Reliability comes from monit watchdogs, because the box has no systemd or cron. Nightly encrypted restic backups go to Google Drive, and an optional VPS mirror keeps a second copy of the dashboard for times the box is frozen. Heavy coding work is handed to other AI agents (Antigravity, Claude Code, Cline, Cursor, Hermes) through one runner, `acp-run`. Every message ends with a unique reference line, so the owner can point at any post.

- **Desks, not one assistant:** each bot has a narrow scope, read-only sources, and a rule to watch, draft and remind.
- **One list a day:** the Chief of Staff merges all the briefs into a single ranked list.
- **Act in one place:** dashboard buttons flow back to the bots through the CLI and a webhook.
- **Private by design:** the dashboard is open only to the owner's tailnet and login, and secrets stay in box secrets.
- **Nothing is sent without approval:** bots draft messages, and the owner approves every send.
- **Self-healing:** monit restarts services, re-applies the Tailscale mapping and alerts after sustained failures.
- **Recoverable:** nightly restic backups to Drive, plus an optional live VPS mirror.
- **Portable:** this repo (its README.md is the main prompt) lets another user's Chief of Staff rebuild the system step by step, personalized to them.
