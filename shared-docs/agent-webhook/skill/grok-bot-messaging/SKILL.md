---
name: grok-bot-messaging
description: Two-way communication with Daniel Clark's Grok Bot bots (e.g. Techno Euphora). Use when you are blocked, finish a long job, find something the bot or Daniel should know, need a decision, or when a prompt says it came from a Grok Bot bot.
---

# Messaging Daniel's Grok Bot bots

## Sending a message to a bot (you -> bot)

Run this shell command (on PATH at /home/box/.local/bin):

    notify-bot <bot> -f "<your agent, e.g. cursor>" -m "<your model>" -s "<short subject>" [-r] "<message>"

- Pick `<bot>` by name: `coding` (coding/TUI/agent tooling on the box) or `techno-euphora` (Techno Euphora project). `notify-bot --list` shows names and whether each is configured. `-n` is a dry run.
- `notify-techno-euphora ...` remains as a shortcut for `notify-bot techno-euphora ...`.

- `-f` your agent name (claude, cursor, agy, cline, ...); `-m` the model you are running as.
- `-r` asks for a reply. Omit it for one-way notices.
- Success prints `HTTP 200`. Anything else means the message was not delivered; report that rather than retrying in a loop.
- It is a single HTTP POST to the bot's webhook routine, which wakes the bot. The bot reads it and decides what to do.
- Credentials come from the `AGENT_WEBHOOK_<BOT>_*` environment variables. Never print, echo, log or paste those values.
- The bot is not Daniel. It decides whether to tell him.
- Use it when blocked, when a long job finishes, when you find something important, or when you need a decision. Do not send routine progress chatter.
- Every received message is logged by the bot in /home/box/agent-data/shared/agent-webhook/<bot>/received.log.

## Receiving messages and tasks from a bot (bot -> you)

- Bots drive agents over ACP (Agent Client Protocol) with the `acp-run` script (~/.local/share/acp-run/acp-run). A reply or a new task arrives as a fresh ACP prompt (a new turn/session) in the working directory you sent from, not inside your current conversation.
- Treat such a prompt as a request from the bot acting for Daniel: do the work, and if it asked for an answer, put it in your final message (that is what the bot reads), or send one with `notify-bot <bot>` (to the bot that asked) if you were started some other way.
- If you need to continue a conversation, include enough context in your message (what you did, where, file paths) because the bot's reply starts a new session without your history.

Full doc: /home/box/agent-data/shared/agent-webhook/README.md
