# Phase 8: Connectors and third-party systems

[Back to README](../README.md) | Previous: [phase-07-cos-routine.md](phase-07-cos-routine.md) | Next: [phase-09-ai-agents.md](phase-09-ai-agents.md)

## How the CoS runs this phase

1. Explain in 2-4 sentences what this phase does and why. Mark what is Grok-Bot-specific vs generic.
2. Show the relevant repo files/examples. Anything marked `EXAMPLE FROM DANIEL, DO NOT USE AS-IS`: show as an example, ask the interview questions, draft the user's own version, get an explicit OK.
3. Ask before running anything that installs, overwrites, sends, spends, exposes a service, or is destructive.
4. Do the steps. Prefer `scripts/install.sh --phase 8` (dry run) then `--apply` for file copies.
5. Run the Verify section and report results honestly.
6. Record progress (PROGRESS.md + memory) and wait for the user before the next phase.

## Guide (from Daniel's working system)

```text
Grok-Bot-specific: connectors are MCP plugins added in the app (Settings -> Connectors, or the add-connector skill). OAuth
connectors ask the user to sign in; bots never see passwords. Multiple accounts = separate namespaces (Daniel: user-Gmail,
user-Gmail--<second-account>, user-Gmail--<third-account>). Give each bot only what it needs. Format: use | auth | setup.
 1. Gmail (x3) | OAuth | one connector per account. Zero (labels, drafts, archive), Radar, Family/Chess/Pika/Business/Friends, CoS. Drafts only.
 2. Google Calendar | OAuth | events, availability, pinned-topic check.
 3. Google Drive | OAuth | archive of old reports; (backups use rclone's own token, phase 12).
 4. GitHub | Grok Bot GitHub connector (OAuth) + cursor-github | Radar (PRs, reviews, issues), Coding, Techno Euphora. For git/gh
    on the box create your own fine-grained PAT and store it as a box secret (GH_TOKEN).
 5. Slack | OAuth | read channels/DMs for work radar; drafts only.
 6. Jira / Confluence | OAuth (Atlassian MCP) | Radar tracks the user's issues (Daniel: Northern.tech/CFEngine). Pick the right site.
 7. Beeper | Beeper Desktop API on the box (beeper-server 127.0.0.1:23374, monit) + Beeper MCP connector; Mac Beeper as fallback |
    SMS/WhatsApp/Signal/Discord for Family/Chess/Pika/Friends. Install Beeper on the box, sign in on the box desktop, enable the
    Desktop API, create an API token in Beeper settings, store it as a box secret, point the connector at it.
 8. X | OAuth X plugin | optional reads.
 9. Saner.ai | no public API: opencli plugin driving the box Chrome's signed-in session (`opencli plugin install
    github:<OWNER>/<PRIVATE_REPO>`) + MCP server saner_mcp.py (in opencli-plugins/saner/mcp/) exposed as connector
    `saner-box`; Mac fallback runs the same MCP behind a bearer-token Caddy gate (Daniel's Mac side used Tailscale Funnel for
    that MCP endpoint so Grok Bot's cloud can reach it; the DASHBOARD must never use Funnel). Used by Other + cos-gather.
    Steps: Node 22 + `npm i -g --prefix ~/.local @jackwener/opencli`; install the OpenCLI Browser Bridge extension in box Chrome;
    sign in to app.saner.ai there; `opencli doctor`; `opencli saner status`; `opencli saner tasks --status open`. wd-saner keeps
    Chrome + bridge alive. For the MCP: own random token (`openssl rand -hex 32`), stored in your secret store and the connector
    auth header. Never reuse anyone else's.
10. Shortwave | no API on Pro: opencli plugin `shortwave` (in opencli-plugins/shortwave/; reads the web app's IndexedDB in box Chrome, read-only) +
    Hermes on the Mac as fallback | Other + cos-gather (titles/links only, keep private). the files listed under 'Repo files' below (`scripts/install.sh --phase N`), then
    `cd ~/.opencli/plugins/shortwave && npm i`, sign in to app.shortwave.com in box Chrome, `opencli shortwave status -f json`.
11. Forgejo | (a) box-local Forgejo (shared/forgejo-box) as private mirror of GitHub repos (forgejo-mirror-github, git-forgejo,
    tea-forgejo, forgejo-api); (b) external Forgejo (Daniel: forgejo.mit.edu, SIPB) via skills ade-forgejo / ade-forgejo-ci
    (examples/skills/ade-forgejo/SKILL.md). Token per instance: Forgejo Settings -> Applications -> Generate token; box secret FORGEJO_TOKEN.
12. Tailscale (phase 10) | 13. Drive backups via rclone (phase 12)
14. Composio (optional SaaS aggregator MCP), Twilio docs MCP (docs only) | 15. Finance (read-only bank data; Money bot; optional)
16. Mac bridge | Grok Bot computer connector on the user's Mac (ListMachines) + Daniel's mac-mcp-forwarder.py on the box
    (127.0.0.1:18800 -> Mac MCP servers over tailnet SOCKS: Hermes, Basic Memory, Saner MCP). Skip without an often-on Mac.
17. Hermes Agent (Mac) | Shortwave fallback + alert channel (hermes-ping -> Telegram).
Rules: tell the user at once when a connector disconnects; fall back box -> Mac; track in shared/source-health.json.
Verify: `scripts/verify.sh 8` and ask each bot for one read with its connectors.
```

## Notes

Connector setup is done by the USER in the Grok Bot app (OAuth). Never ask for passwords or tokens in chat; tokens go into box env secrets. Saner MCP README: [opencli-plugins/saner-README.md](../opencli-plugins/saner-README.md).

## Repo files and where they go (14 files)

Copy with `scripts/install.sh --phase 8` (dry run; add `--apply` to write; existing files are kept unless `--force`, which makes a `.bak-<timestamp>` first). Manual equivalent: `install -D -m <mode> <repo_path> <destination>`.

| repo path | destination | mode |
|---|---|---|
| [`opencli-plugins/saner/mcp/GROK_PROMPT.md`](../opencli-plugins/saner/mcp/GROK_PROMPT.md) | `/home/box/.opencli/plugins/saner/mcp/GROK_PROMPT.md` | 644 |
| [`opencli-plugins/saner/mcp/deploy/Caddyfile`](../opencli-plugins/saner/mcp/deploy/Caddyfile) | `/home/box/.opencli/plugins/saner/mcp/deploy/Caddyfile` | 644 |
| [`opencli-plugins/saner/mcp/deploy/auth-proxy.sh`](../opencli-plugins/saner/mcp/deploy/auth-proxy.sh) | `/home/box/.opencli/plugins/saner/mcp/deploy/auth-proxy.sh` | 755 |
| [`opencli-plugins/saner/mcp/deploy/install.sh`](../opencli-plugins/saner/mcp/deploy/install.sh) | `/home/box/.opencli/plugins/saner/mcp/deploy/install.sh` | 755 |
| [`opencli-plugins/saner/mcp/deploy/smoke-test.sh`](../opencli-plugins/saner/mcp/deploy/smoke-test.sh) | `/home/box/.opencli/plugins/saner/mcp/deploy/smoke-test.sh` | 755 |
| [`opencli-plugins/saner/mcp/saner_mcp.py`](../opencli-plugins/saner/mcp/saner_mcp.py) | `/home/box/.opencli/plugins/saner/mcp/saner_mcp.py` | 755 |
| [`opencli-plugins/shortwave/.gitignore`](../opencli-plugins/shortwave/.gitignore) | `/home/box/.opencli/plugins/shortwave/.gitignore` | 644 |
| [`opencli-plugins/shortwave/README.md`](../opencli-plugins/shortwave/README.md) | `/home/box/.opencli/plugins/shortwave/README.md` | 644 |
| [`opencli-plugins/shortwave/lib/common.js`](../opencli-plugins/shortwave/lib/common.js) | `/home/box/.opencli/plugins/shortwave/lib/common.js` | 644 |
| [`opencli-plugins/shortwave/lib/session.js`](../opencli-plugins/shortwave/lib/session.js) | `/home/box/.opencli/plugins/shortwave/lib/session.js` | 644 |
| [`opencli-plugins/shortwave/opencli-plugin.json`](../opencli-plugins/shortwave/opencli-plugin.json) | `/home/box/.opencli/plugins/shortwave/opencli-plugin.json` | 644 |
| [`opencli-plugins/shortwave/package.json`](../opencli-plugins/shortwave/package.json) | `/home/box/.opencli/plugins/shortwave/package.json` | 644 |
| [`opencli-plugins/shortwave/status.js`](../opencli-plugins/shortwave/status.js) | `/home/box/.opencli/plugins/shortwave/status.js` | 644 |
| [`opencli-plugins/shortwave/todos.js`](../opencli-plugins/shortwave/todos.js) | `/home/box/.opencli/plugins/shortwave/todos.js` | 644 |

## Verify

```bash
scripts/verify.sh 8
```

Done when the checks pass and the user agrees. Then update PROGRESS.md: `phase 8 done <date>; next 9`.
