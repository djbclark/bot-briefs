# Talking to the operator's Grok Bot bots (agent webhook)

Any agent on this box (Claude, Cline, agy, Cursor, scripts) can start a conversation with one of the operator's Grok Bots by name:

    notify-bot <bot> -f "<your agent, e.g. cline>" -m "<model>" -s "<short subject>" [-r] "message"
    notify-bot --list            # bot names and whether their webhook is configured (yes/no)

Bots (registry, no secrets: ~/.config/agent-webhook/bots.json):

| bot | what it is for | received log |
|---|---|---|
| `coding` | Coding bot: coding/TUI/agent-tooling work on the box (acp-run, agy, Claude Code, Cursor, Cline) | coding/received.log |
| `project-bot` | Project Bot (repo <ORG>/<PROJECT_REPO>, ~/src/project-bot) | project-bot/received.log |
| `chief-of-staff` | Chief of Staff "Dashboard dispatch" routine: the Bot Briefs dashboard calls it on Send / Submit all (`-s "Dashboard submit"`) | chief-of-staff/received.log |

- `-r` means you want a reply. The bot answers by starting a new ACP turn (acp-run) in your current working directory.
- `-n` dry run: prints the payload and which env var names would be used; sends nothing.
- One HTTP POST to the bot's webhook routine, which wakes the bot. The bot decides what to do and whether to tell the operator.
- Use it when blocked, when a long job finishes, when you find something important, or need a decision. No routine progress chatter.
- `notify-bot <bot> ...` sends to any configured bot.

Payload (JSON): `to, from, model, subject, message, reply_wanted, cwd, sent_at`.

Secrets (env, never print): `AGENT_WEBHOOK_<BOT>_URL`, `AGENT_WEBHOOK_<BOT>_KEY`, optional `AGENT_WEBHOOK_<BOT>_HEADER` (full header line; default `Authorization: Bearer <KEY>`). BOT = name uppercased, `-` -> `_` (CODING, PROJECT_BOT). project-bot alone falls back to the unsuffixed `AGENT_WEBHOOK_URL/_KEY/_HEADER`.

They can come from the process environment (box secrets) or from `~/.config/agent-webhook/secrets.env` (chmod 600, `NAME=value` lines, read by notify-bot on every run; environment values win). The file is the easy way to add a webhook without restarting long-running callers such as the dashboard server.

## Registered agents

| `-f` value | Models seen | Bot's reply command |
|---|---|---|
| claude | (Claude models) | `acp-run claude -C <cwd> -p ...` |
| agy | gemini-3.8-flash | `acp-run agy -C <cwd> -p ...` |
| cline | claude-sonnet-4.5, deepseek-v4.1-flash | `acp-run cline -C <cwd> -p ...` |
| cursor | composer-2.5 | `acp-run cursor --model composer-2.5 -C <cwd> -p ...` (runs `cursor-agent acp`) |

## received.log format

Per bot: /home/box/agent-data/shared/agent-webhook/<bot>/received.log (the old top-level received.log is now a symlink to project-bot/received.log). One line per message, fields separated by ` | `:

    YYYY-MM-DD HH:MM:SS TZ | from=<agent> | model=<model> | subject=<subject> | message=<text> | action=<what the bot did>
