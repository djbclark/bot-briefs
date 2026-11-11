# <PRIVATE_REPO>

An [OpenCLI](https://github.com/jackwener/OpenCLI) plugin for [Saner.AI](https://www.saner.ai/). It lets you and your AI agents ask Skai questions and read your Saner conversations, tasks and notes from the terminal, using the Saner session already signed in to your Chrome.

Saner.AI has no public API or MCP server (checked September 2026). This plugin calls the same web-app endpoints `app.saner.ai` uses, with your own browser session. It does not drive the UI and never takes window focus.

```console
$ opencli saner ask "What should I focus on today?"
$ opencli saner tasks --status open
$ opencli saner search "project plan" -f yaml
```

## Install

Requirements: OpenCLI ≥ 1.8 with the Browser Bridge extension connected (`opencli doctor`), and a Chrome profile signed in to <https://app.saner.ai/>.

```bash
opencli plugin install github:<OWNER>/<PRIVATE_REPO>
opencli saner status
```

## Commands

| Command | Access | What it does |
|---|---|---|
| `status` | read | Login state, plan, remaining daily message / AI-request quota, current conversation |
| `ask <prompt>` | **write** | Ask Skai and print the answer. Continues the current conversation unless `--new` |
| `conversations [query]` | read | List (or search) conversations; `--kind chat\|proactive\|all`, `--limit` |
| `read [id]` | read | Messages of a conversation (default: the one `ask` continues); `--last N` |
| `tasks` | read | Tasks; `--status open\|done\|all`, `--due`, `--limit` |
| `notes` | read | Recently updated notes; `--limit` |
| `note <id>` | read | One note rendered as text (ProseMirror and HTML bodies both converted) |
| `search <query>` | read | Note search; `--mode smart` (keyword, default) or `--mode semantic` |

IDs are UUIDs, but any unique prefix of at least 4 characters works (`opencli saner read a1b2`), much like git short hashes.

### `ask` options

1. `--new`: start a fresh conversation. Put the prompt **before** it, or write `--new true`: OpenCLI gives boolean flags an optional value, so `ask --new "hi"` reads `"hi"` as the flag's value and then reports the prompt as missing.
2. `--conversation <id>`: continue a specific conversation. This also becomes the current one.
3. `--bot <name>`: choose who answers.
   1. `skai` (default) knows your notes and tasks.
   2. `online` searches the web.
   3. An external model id Saner offers, e.g. `gpt-5.4`, `grok-4`, `claude-opus-4.6` or `gemini-3.1-pro`. External models run on **your own provider API key**, set in Saner under Settings → My AIs. They don't count against Saner's message quota, but they also **can't see your notes or tasks**.
4. `--reasoning [true|false]`: Skai's reasoning mode (default **on**). It is slower but better at multi-step questions, and it has no effect on other bots.
5. `--timeout <s>`: how long to wait for the answer (default 120). If the answer is cut off, the partial text is saved, returned and labelled as partial.

To change the defaults without passing flags, set `OPENCLI_SANER_BOT` (e.g. `grok-4`) and `OPENCLI_SANER_REASONING=false`.

### Choosing a bot

The default is **Skai with reasoning**. On 2026-09-24 each available bot was asked three questions, each in its own fresh conversation:
1. List my open tasks with due dates. This is grounded in Saner data, and the correct answer has 3 tasks.
2. A date-arithmetic question: "every 3rd weekday", 5th meeting, correct answer 2026-10-14.
3. Repeat the previous answer in an exact format, which tests conversation memory and format following.

| Bot | Tasks (grounded) | Date reasoning | Memory + format | Seconds per answer |
|---|---|---|---|---|
| **`skai --reasoning`** | ✅ 3/3 | ✅ | ✅ | 10–17 |
| `skai` | ✅ 3/3 | ❌ (treated it as every weekday) | ✅ | 7–9 |
| `grok-4` | ❌ no access | ✅ | ✅ | 4–9 |
| `gpt-5.4` | ❌ no access | ❌ | ✅ | 4–5 |
| `claude-opus-4.6` | could not run: Anthropic key has no credit | | | |
| `gemini-3.1-pro` | could not run: Google API key invalid | | | |

Only Skai sees your Saner data, which is the point of this plugin, and reasoning mode fixed its one miss for the same quota cost (1 message). For a general question that doesn't need your notes, `--bot grok-4` answered fastest. That's one probe per category, so treat it as a sanity check rather than a benchmark.

Plain output (the default for `ask`) is the answer text only. Skai's interactive UI blocks are rendered as text: option lists and references are printed, and editors (task/note proposals) are summarized with a pointer to the web app, because applying them is a UI action.

The current conversation id is kept in `~/.opencli/saner/state.json` (override with `OPENCLI_SANER_STATE`). That file holds only an id, never session data.

## MCP server

`mcp/saner_mcp.py` exposes the same functions as MCP tools: `saner_status`, `saner_tasks`, `saner_search`, `saner_note`, `saner_notes`, `saner_conversations`, `saner_read` and `saner_ask`. It is a stdio server, run with `uv run --script`.

1. It calls `~/.hermes/bin/saner` when that exists, so it gets the shared OpenCLI profile lease; otherwise it calls `opencli saner`.
2. Each call uses its own conversation pointer. So `saner_ask` starts a new conversation unless it is given a `conversation_id`, and it returns the id so the client can follow up.

`mcp/deploy/` publishes it for remote agents. It is the same pattern as the Hermes MCP bridge:
1. mcp-proxy listens on `127.0.0.1:18792`, in front of the stdio server.
2. A Caddy bearer gate listens on `127.0.0.1:18793`. One token per client is kept in Keychain as `<keychain-service>-<client>`.
3. Tailscale Serve (tailnet-only, preferred) or Funnel serves the gate at `https://<mac>.<tailnet>.ts.net:8443/<path>`.

To install:

```bash
bash mcp/deploy/install.sh     # LaunchAgents com.example.saner-mcp-{bridge,auth}
tailscale serve --bg --https=8443 --set-path /<path> http://127.0.0.1:18793   # tailnet-only (preferred); use `funnel` only if a client cannot join the tailnet
bash ~/.local/libexec/saner-mcp-bridge/smoke-test.sh <url-ending-in-/mcp>
```

The public URL should return 401 without a token. From the Mac itself, requests to the public hostname go out through Tailscale's Funnel relays, so `smoke-test.sh <public-url>` does exercise the real public path. If it fails everywhere, check Funnel ingress first: on 2026-09-24 it died on every port, and restarting the Tailscale app fixed it. `mcp/GROK_PROMPT.md` is the usage prompt given to the Grok "Chief of Staff" bot.

**Tokens: one per client.** The gate accepts one bearer token per client (for example `grokbot` and `laptop`), each in its own Keychain item (`<keychain-service>-<client>`). `auth-proxy.sh` reads them at startup and writes none to disk; a missing token is replaced by a random value so an empty `Bearer ` never matches.
1. **Never share a token** between services (an earlier version also accepted another service's token; that was removed).
2. **Rotate** each token on a schedule and after any suspected leak, then `launchctl kickstart -k gui/$(id -u)/<launchd-label>`.
3. **Prefer tailnet-only `tailscale serve`** with ACLs; use Funnel only for clients that cannot join the tailnet.
4. **Keep mcp-proxy on 127.0.0.1** with origin/host checks enabled (see `install.sh`).

## How it works

1. **Auth.** The command reads Saner's Supabase session cookie (`sb-server-auth-token`, possibly chunked) through the Browser Bridge's cookie API and sends its access token as a `Bearer` header. Nothing is written to disk.
2. **Token refresh.** If the access token is expired or close to it, the plugin loads `app.saner.ai` in its background adapter tab and waits for Saner's own client to refresh it. It deliberately never uses the refresh token itself: Supabase rotates refresh tokens, so a refresh the browser doesn't see would sign you out of the web app.
3. **Chat is client-orchestrated.** The Saner web app, not its server, saves both sides of each exchange. `ask` replays that sequence exactly: create the conversation, save your message, stream the answer over SSE, update the title, and save the answer. The result looks the same in the web app as a message typed there. Details are in [docs/PROTOCOL.md](docs/PROTOCOL.md).

## Caveats

1. These are private, undocumented endpoints. Saner can change them at any time, and the plugin surfaces this as typed errors instead of hiding it.
2. `ask` counts against your Saner daily message quota (see `opencli saner status`).
3. The Chrome profile OpenCLI uses must be the one signed in to Saner. The standalone Saner desktop/app-mode window may hold a separate session.

## Development

```bash
git clone https://github.com/<OWNER>/<PRIVATE_REPO> && cd <PRIVATE_REPO>
npm install                        # postinstall links the host OpenCLI into node_modules
npm test                           # unit tests (synthetic fixtures only)
```

The live `saner` plugin on the operator's machine is the **pinned GitHub install**, because Hermes depends on it through `~/.hermes/bin/saner`. Ship a change by pushing and then running `opencli plugin update saner`. To try unpushed code live, temporarily swap in the checkout, and swap back when done:

```bash
opencli plugin uninstall saner && opencli plugin install "$PWD"                      # live = this checkout
opencli plugin uninstall saner && opencli plugin install github:<OWNER>/<PRIVATE_REPO>   # back to pinned
```

Layout: top-level `*.js` files are commands (keep non-command `.js` files out of the root: `opencli plugin list` shows every root `.js` as a command). OpenCLI flat-scans the plugin root and imports any file that calls `cli(`, so helpers live in `lib/` and tests in `test/`, where discovery never looks.

Why the link matters: a plugin must import the running OpenCLI's registry. A second copy of `@jackwener/opencli` in `node_modules` would register the commands somewhere nobody reads. `opencli plugin install` creates this link itself, and `scripts/link-host.mjs` restores it after a local `npm install`.

## License

MIT
