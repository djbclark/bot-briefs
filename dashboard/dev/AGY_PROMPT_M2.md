# M2 build spec: item actions, bot inbox/ack, wake-now, publish improvements

You are working in /home/box/agent-data/shared/dashboard (Python package `briefs/`, FastAPI + Jinja2 + htmx, SQLite).
Read README.md, briefs/db.py, briefs/store.py, briefs/web.py, briefs/cli.py, briefs/config.py, templates and tests first.
Run tests with `.venv/bin/pytest -q`. All existing tests must keep passing; add new ones.

HARD RULES
- Do NOT touch data/briefs.db or anything under data/. Tests use temp DBs only (see tests/conftest.py).
- Do NOT run `notify-bot` for real, ever. Tests must use a stub command via env BRIEFS_NOTIFY_CMD.
- Do NOT start servers on port 8642, do not edit ops/, README.md, ~/.bashrc, or anything outside this repo. No git commands.
- Never log or store secrets: for notify calls store only the exit code and a short status word, never stdout/stderr.
- Keep the existing auth middleware behaviour (Tailscale-User-Login gate, localhost exception) intact.

## 1. Bot names (briefs/config.py)
Add a canonical CLI/notify name per bot and make find_bot() accept it:
cos->chief-of-staff, radar->radar, zero->zero, family->family, money->money, business->business, chess->chess,
pika->pika, friends->friends, project->project-bot, oneoffs->one-offs, coding->coding.
Add field `cli_name` to Bot (keep slug unchanged; URLs stay /b/<slug>). Add helper `bot_cli_name(bot)`.

## 2. Schema (briefs/db.py) - idempotent migration in init
`actions` already exists. Add missing columns with `ALTER TABLE ... ADD COLUMN` only if absent (check PRAGMA table_info):
bot_id TEXT, wake_requested INTEGER DEFAULT 0, wake_status TEXT DEFAULT '', seen_at TEXT, acked_at TEXT.
Add index on actions(bot_id, status). Keep status CHECK (pending, seen, applied, failed).
items.snooze_until exists already; item statuses used: open, resolved, done, dismissed (and whatever exists already).

## 3. Actions layer (new module briefs/actions.py)
`create_action(conn, item_id, type, payload: dict, author: str) -> dict` where type in
{done, dismiss, snooze, priority, reply, reopen}. It inserts the action row (bot_id = item's bot_id, status pending,
created_at/updated_at ISO ET with offset) AND applies the local effect immediately, in one transaction:
- done: items.status='done'. dismiss: items.status='dismissed'.
- snooze: payload {"until": "YYYY-MM-DD"} (UI offers 1d, 3d, 1w from today ET, or a date input). items.snooze_until=until.
  Validate the date (must be > today ET).
- priority: payload {"delta": -1|+1}; "up" = more important = delta -1. items.priority treats NULL as 3, clamp 1..5.
- reply: payload {"text": "...", "wake": bool}; text required, max 4000 chars. No item change.
- reopen: items.status='open', snooze_until=NULL (undo for done/dismiss/snooze).
Validation errors raise ValueError with a human message.
Also: `list_actions_for_item(conn, item_id)`, `inbox(conn, bot, include_seen=False, mark_seen=True) -> list[dict]`
(pending actions for the bot, oldest first; marks them seen with seen_at; each entry includes action id, type, payload,
created_at, author, status, and item: id, item_key, title, status, snooze_until, priority, links, latest display_id and
report_date, dashboard url `{BRIEFS_BASE_URL}/i/<id>`), and
`ack(conn, action_id, status, note) -> dict` (status applied|failed, note max 2000 chars, sets acked_at; ValueError if unknown id).

The OPEN view everywhere (today cards, bot page, CoS list, `brief items --open`, /api/items) must exclude
status in (done, dismissed, resolved) AND items with snooze_until > today ET (so they reappear automatically when the
snooze ends). Add a "Snoozed" filter/section on the bot page listing snoozed items with their until date.
Check store.py publish/import: republishing must NEVER reopen an item whose status is done or dismissed, and must not
clear snooze_until. Add tests for that.

## 4. Wake-now (briefs/wake.py)
`notify_configured(cli_name) -> bool`: run `[NOTIFY_CMD, "--list"]` (timeout 10s), parse lines `<name> configured=yes|no`.
`wake_bot(conn, action_id)`: if not configured -> wake_status='not_configured'. Else run
`[NOTIFY_CMD, cli_name, "-f", "dashboard", "-m", "none", "-s", f"Dashboard action #{id}", message]` with timeout 30s,
stdout/stderr captured and discarded; wake_status 'sent' on exit 0 else 'failed' (store `failed (exit N)`).
message = f"The operator left a dashboard action for you: #{id} ({type}) on \"{title[:80]}\". Run `brief inbox --bot {cli_name} --json`, handle it under your normal rules, then `brief ack {id} --status applied|failed --note \"...\"`."
NOTIFY_CMD = env BRIEFS_NOTIFY_CMD or /home/box/.local/bin/notify-bot. Run wake in a FastAPI BackgroundTask so the
POST returns fast; set wake_status='sending' first. Any action type may request wake (checkbox), not only reply.

## 5. Web (briefs/web.py + templates)
UI POST routes (same login gate as pages), all returning an HTML partial for htmx swap:
- POST /i/{item_id}/action  form fields: type, until (snooze, either "1d"/"3d"/"1w" or YYYY-MM-DD), delta, text, wake ("on").
- Response: re-rendered item action panel (status badge, buttons, history), plus HX-Trigger so lists can refresh.
CSRF: every POST must have header `HX-Request: true` AND an Origin header (or Referer if no Origin) whose
scheme+host[:port] equals the request's Host (use X-Forwarded-Host if present, else Host; accept both http/https scheme
for that host). Otherwise 403. Write tests for: missing HX-Request -> 403, cross-origin Origin -> 403, same-origin -> 200.

Item action panel partial (templates/partials/item_actions.html), used on the item page (/i/<id>) and expandable
from item rows on Today / bot / report pages (a small "⋯" or "Act" button that hx-get's the panel into the row; route
GET /i/{item_id}/actions returns the panel). Mobile-first: big tap targets (min 40px), buttons wrap, works at 390px.
Panel content:
- Current status (open/done/dismissed/snoozed until X), priority.
- Buttons: Done, Dismiss, Snooze 1d / 3d / 1w / date picker + Snooze, Priority ↑ / ↓, Reopen (when not open).
- Reply/instruction textarea + "Wake bot now" checkbox + Send. Show next to the checkbox whether wake is available
  for this bot (notify configured) or "will be picked up next run". Cache notify_configured results ~60s.
- Action history list: newest first, type + payload summary, created time (ET, short), status badge
  pending/seen/applied/failed, bot_note, wake_status ("woke bot" / "will be picked up next run" / "wake failed").
On Today/bot views, after done/dismiss the row disappears immediately (htmx: swap the row out, e.g. hx-target closest
row + hx-swap="outerHTML" returning an empty/"Done ✓ (undo)" stub). Snooze also removes it from the open view.

Local-only bot endpoints (reject with 403 if the request carries any Tailscale-* / X-Forwarded-* header or the client
is not localhost, even with a valid login):
- GET  /api/inbox?bot=<name>&include_seen=0|1&peek=0|1  -> JSON list (marks seen unless peek=1)
- POST /api/actions/{id}/ack  JSON {"status": "applied"|"failed", "note": "..."} -> JSON action
- GET  /api/items/{id}/actions -> JSON history
These JSON endpoints are exempt from the HX-Request/Origin CSRF rule (they are local-only instead).

## 6. CLI (briefs/cli.py)
- `brief inbox --bot <name> [--json] [--include-seen] [--peek]` -> pending actions; marks them seen (unless --peek).
  Human output: one block per action. JSON: list as from actions.inbox. Exit 0 with [] when empty.
- `brief ack <action_id> [<action_id> ...] --status applied|failed [--note "..."]` -> prints JSON of updated actions.
- `brief actions --item <id>` -> history JSON (handy for debugging).
- publish: add `--md FILE` (alias of --file) and keep `--items-json FILE`; when both are given, the markdown is the
  report body and items come from the JSON (keys optional; fallback per-row key -> links -> title hash, already
  implemented in parse_items_json_data). Add `--from-sheet-rows FILE`: JSON list of reply-sheet rows exactly as bots
  build them: `[ID, Item, Status/details, Your reply, Links]` (5 cols) or `[ID, Item, Status/details, Links]` (4 cols).
  Skip a header row (row[0] case-insensitively "id"). Links cell may be: a URL string, several URLs/labels separated
  by newlines, a `=HYPERLINK("url","label")` formula string, a list of URLs, list of [label,url], or list of
  {label,url}. Optional trailing extra element (6th for 5-col, 5th for 4-col) is NOT supported - keys come from links
  / title hash; a dict row form {"id","item","details","links","key"} is also accepted (reuse items-json parser).
  Ignore the "Your reply" column. `--items-json` and `--from-sheet-rows` are mutually exclusive.
- `--date` default today ET; `--kind` choices add "weekly" already there. Print JSON result as now.
- Bots call `/home/box/bin/brief` which runs `python -m briefs.cli` with PYTHONPATH set; keep that working.
- Every command must fail with a clear one-line error and non-zero exit (no tracebacks) on bad input / unknown bot.

## 7. Tests (tests/)
Add tests covering: migration idempotent on an existing DB without new columns; each action type's local effect;
snooze hides then reappears (monkeypatch today); done/dismiss hidden from open view; republish does not reopen done
item; inbox marks seen and include_seen; ack applied/failed + bad status; web POST for each action type with
Tailscale-User-Login header + HX-Request + same Origin; CSRF failures; local-only endpoints reject proxied requests;
wake with stub notify (configured -> 'sent', exit 1 -> 'failed', not listed -> 'not_configured'), using a stub script
written to tmp_path and BRIEFS_NOTIFY_CMD; CLI inbox/ack/publish --md + --items-json and --from-sheet-rows
(5-col with header and HYPERLINK formula links) against a temp DB.

When finished, reply with: files changed, test count, and anything you could not do.
