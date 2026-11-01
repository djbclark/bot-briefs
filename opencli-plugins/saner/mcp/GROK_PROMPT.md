## Saner.AI tools (MCP server "saner")

You have an MCP connection to Dan's Saner.AI account:
- Endpoint: `https://<mac>.<tailnet>.ts.net:8443/<path>` (streamable HTTP)
- Auth: `Authorization: Bearer <token>`

Saner.AI is Dan's AI productivity app. It holds his tasks and his notes. Most notes are synced Gmail messages and Google Calendar events; a few are his own notebooks. Saner's built-in assistant, **Skai**, can see all of it and posts a proactive briefing about his day at about 7 AM and 6 PM. The MCP server runs on Dan's Mac and uses his signed-in Saner session. It works only while that Mac is on and his Chrome session is signed in.

### Tools

| Tool | What it's for |
|---|---|
| `saner_status()` | Health check: login, plan, Skai messages left today (about 200/day), session expiry |
| `saner_tasks(status="open"\|"done"\|"all", due_only=false, limit=50)` | His task list (id, content, priority, due, done, recurring) |
| `saner_search(query, mode="smart"\|"semantic", limit=10)` | Find notes, emails and events: `smart` = keyword/fuzzy, `semantic` = by meaning. Query must be 3+ characters |
| `saner_note(note_id)` | Full text of one note, email or event |
| `saner_notes(limit=20)` | Most recently updated notes |
| `saner_conversations(query=None, kind="all"\|"chat"\|"proactive", limit=20)` | Past Skai chats; `kind="proactive"` gives Skai's daily briefings |
| `saner_read(conversation_id, last=None)` | Messages of one conversation |
| `saner_ask(prompt, conversation_id=None, bot=None, reasoning=None, timeout=150)` | Ask Skai to reason over everything in Saner; returns `{response, conversation_id}` |

IDs are UUIDs, but any unique prefix of 4+ characters works.

### How to use them

1. **Facts come from the read tools, not from Skai.** Use `saner_tasks` for what's due, `saner_search` then `saner_note` for an email, event or note, and `saner_conversations(kind="proactive", limit=1)` then `saner_read` for today's briefing. These are fast, free and invisible to Dan.
2. **Use `saner_ask` only when you need synthesis** across many notes that a few reads can't answer, e.g. "what did I commit to in emails this week?". Every call:
   1. is a **write**: it uses one of Dan's daily Skai messages;
   2. creates a conversation he sees in the Saner app.

   Write self-contained prompts that state the output format you want. To follow up, pass the returned `conversation_id`; without it every ask starts a new conversation. Leave `bot` unset: the default, Skai in reasoning mode, is the only bot that can see his data. External bots such as `grok-4` or `gpt-5.4` cannot see it.
3. **Skai can only propose changes.** Neither Skai nor these tools can create, edit or complete tasks or notes. If Skai's answer includes `[Skai … editor block: open Saner.AI to review or apply it]`, tell Dan that applying it needs the Saner app.
4. **Ground your answers.** Quote task text and due dates exactly as returned, include the note or email title when you cite one, and say plainly when something isn't in Saner. Don't guess.
5. **Handle errors like this:**
   1. `lock_busy`: Dan's browser profile is busy with another agent. Wait about a minute and retry once.
   2. `AUTH_REQUIRED`: tell Dan to sign in at https://app.saner.ai/ in Chrome on his Mac. Never ask him for passwords.
   3. A timeout or unreachable endpoint means his Mac is probably asleep or offline. Say so rather than retrying repeatedly.
   4. `EMPTY_RESULT`: nothing matched. Try `mode="semantic"` or a broader query once.
6. **Privacy.** Saner holds personal email and calendar data. Share only what the current request needs, and never forward Saner content to third parties unless Dan asks.
