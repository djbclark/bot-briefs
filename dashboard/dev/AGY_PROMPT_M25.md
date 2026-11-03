# Task: Bot Briefs dashboard "M2.5" UX rework + action lifecycle

Repo: /home/box/agent-data/shared/dashboard (FastAPI + Jinja2 + htmx 2 (vendored at briefs/static/htmx.min.js) + SQLite).
Read README.md section 8, briefs/web.py, briefs/actions.py, briefs/wake.py, briefs/store.py (today_view, items_open,
items_snoozed), briefs/cli.py, briefs/db.py, the templates and briefs/static/style.css, and tests/ before changing anything.
Run tests with: `cd /home/box/agent-data/shared/dashboard && .venv/bin/pytest -q`. All existing tests must keep passing
(update tests whose expectations legitimately change because of this spec, e.g. the old wake checkbox / old action panel).

## HARD RULES
- Do NOT touch data/ (the live DB), do NOT start anything on port 8642, do NOT run git, do NOT run `notify-bot` for real
  (tests must use BRIEFS_NOTIFY_CMD pointing at a stub script in tmp_path). Do not edit files outside this repo.
- Never log or print secrets/env values. Keep the Tailscale-User-Login gate (existing middleware) on every new page/endpoint.
- Every new state-changing browser endpoint (POST) must call the existing `_verify_csrf` (HX-Request: true + same-origin
  Origin/Referer). Local-only /api endpoints use `_verify_local_only`.
- You may run a throwaway server on port 8650+ with BRIEFS_DB pointing at a COPY in /tmp if you want to look at pages.

## 1. Data model / lifecycle (db.py, actions.py)
Action statuses become: `queued`, `submitted`, `processing`, `applied`, `discuss`, `failed`, plus legacy `pending`, `seen`.
The actions table has a CHECK constraint on status that must be widened: migrate by rebuilding the table when the existing
CHECK doesn't allow 'queued' (create actions_new with the new CHECK and all columns, copy rows, drop, rename, recreate
indexes) inside a transaction; idempotent. Add column `rank INTEGER` (nullable). Keep all existing columns.

Two kinds of actions:
- **Icon actions** (types done, dismiss, snooze, priority, reopen): local effect applied immediately (as today) and the action
  is stored with status `pending` (an FYI the bot reads on its next run via `brief inbox`). They never appear in Submit all
  or In progress.
- **Text actions** (type `reply`, payload {"text": ...}, 1..4000 chars): created either as `queued` (Queue button) or
  `submitted` (Send / Submit all). No local effect on the item.

Functions in actions.py (keep existing ones working; adjust signatures as needed):
- `create_action(conn, item_id, type, payload, author, status=None)`: icon types -> 'pending'; reply -> status must be
  'queued' or 'submitted' (default 'queued').
- `importance_key(conn, item_id) -> tuple`: sort key, smaller = more important:
  (0 if the item is in the latest Chief of Staff report else 1, its position in that CoS report (else 0),
   priority if not NULL else 3, due_at if set else '9999-12-31', 0 if the item's latest report_item flags contain NEW else 1,
   first_seen (older first), item_id).
  CoS = bot slug 'cos'. "Latest CoS report" = newest reports row for the cos bot by report_date, id.
- `rerank_submitted(conn)`: assign rank = 1..N over ALL actions with status 'submitted', ordered by importance_key of their
  item then created_at, id.
- `submit_text(conn, item_id, text, author) -> action`: create 'submitted' reply action, rerank, return it.
- `queue_text(conn, item_id, text, author) -> action`: create 'queued' reply action.
- `submit_all(conn, drafts: list[{"item_id": int, "text": str}], author) -> list[action]`: in ONE transaction create a
  'submitted' reply action per non-empty draft, flip every 'queued' action to 'submitted', rerank; return the submitted
  actions of this batch ordered by rank. Empty drafts are ignored; unknown item ids raise ValueError (nothing written).
- `mark(conn, action_id, status)`: only 'processing' allowed for now (from queued/submitted/pending/seen/discuss); sets
  updated_at.
- `ack(conn, action_id, status, note, keep_open=False)`: status in applied|failed|discuss. On `applied` for a `reply` action
  with an item and keep_open False: set the item status to 'done' and meta_json["handled_by_action"] = action id (the user can
  Undo via the existing reopen action). Applied icon actions don't change the item. discuss/failed don't change the item.
- `list_actions(conn, status=None, bot=None) -> list[dict]`: JSON-ready entries (same shape as inbox entries: id, type,
  payload, status, rank, created_at, author, bot (cli name), bot_note, wake_status, item{id,item_key,title,display_id,
  links,url,bot}, and `history`: the earlier actions on the same item, oldest first, each {id,type,payload,status,bot_note,
  created_at}). Order: rank ASC NULLS LAST, created_at, id.
- `inbox(conn, bot, include_seen=False, peek=False)`: for scheduled runs. Returns that bot's actions with status in
  (pending, queued, submitted) (+ seen and processing when include_seen) in the same entry shape, ordered rank then
  created_at. Unless peek: pending -> seen, queued/submitted -> processing (so the dispatcher and the bot don't both handle
  them). Sets seen_at.
- `in_progress(conn) -> list[dict]`: one entry per item whose LATEST reply action has status in (submitted, processing,
  discuss): item fields (id, title, display_id, bot name/slug, links), the latest reply action (id, text, status, bot_note,
  updated_at) and the item's previous reply actions (for showing the conversation). Ordered by importance_key.
- `queued_count(conn) -> int`.

Items whose latest reply action is submitted/processing/discuss are EXCLUDED from the main open lists (they show in
In progress instead). Items with a 'queued' reply show in the list with a small "queued: <text…>" chip.

## 2. Processing trigger (wake.py)
- `dispatch_configured()` -> bool: `notify_configured("chief-of-staff")` using the existing `--list` parsing (keep the
  60 s cache, add a way to clear it).
- `trigger_dispatch(db_path, action_ids)`: runs
  `[cmd, "chief-of-staff", "-f", "dashboard", "-m", "none", "-s", "Dashboard submit", f"{n} actions submitted"]`
  (n = len(action_ids); singular "1 action submitted"), stdout/stderr DEVNULL, timeout 30, and records wake_status on those
  actions: 'dispatch sent' | 'dispatch failed (exit N)' | 'dispatch not configured'. Run it as a FastAPI BackgroundTask
  from Send and Submit all. Never include secrets anywhere.
- Remove the per-reply "Wake bot now" checkbox from the UI (keep wake_bot() for compatibility; it is no longer called by the UI).

## 3. Web endpoints (web.py) — all behind the login gate, POSTs behind _verify_csrf
- `GET /` main view (filters below). Renders base layout + submit bar + banner + In progress container + list.
  When HX-Request and HX-Target == "item-list" return only the list partial.
- `POST /i/{id}/send` form `text` -> submit_text + background trigger_dispatch. Response: empty 200 for the row
  (so the row is removed with hx-swap="outerHTML") and header `HX-Trigger: {"inprogressChanged": true, "draftClear": {"item": id}}`.
- `POST /i/{id}/queue` form `text` -> queue_text. Response: the re-rendered compact row (now showing the queued chip) and
  `HX-Trigger: {"queueChanged": true, "draftClear": {"item": id}}`.
- `POST /submit-all` JSON body or form field `drafts` = JSON list of {item_id, text}. -> submit_all + one background
  trigger_dispatch for all returned ids. Response: small HTML fragment "Submitted N" and
  `HX-Trigger: {"inprogressChanged": true, "listChanged": true, "queueChanged": true, "draftsSubmitted": [item ids]}`.
  If nothing to submit return 400 with a short message.
- `GET /submit-bar` partial: the Submit all button with the server-side queued count in a data attribute
  (`data-queued="N"`); client JS adds the number of non-empty localStorage drafts and shows "Submit all (N)" (disabled when 0).
  The bar reloads on `queueChanged from:body`.
- `GET /in-progress` partial: the In progress section. If no entries: an EMPTY placeholder
  `<div id="in-progress" hx-get="/in-progress" hx-trigger="inprogressChanged from:body" hx-swap="outerHTML"></div>` (no polling).
  If entries: same id, `hx-trigger="every 15s, inprogressChanged from:body"`, a heading "In progress (N)", and per item:
  title (link to /i/{id}), bot name, the user's latest text, a status pill (submitted/processing/discuss), the bot's latest
  note (for discuss, label it "Bot asks:"), a collapsed "earlier" list of previous exchanges, and the same single-line text
  box + Send button (no Queue needed here). When the set of item ids differs from the request's `ids` query param (the
  client sends the ids it currently shows via hx-vals or a hidden input), add `HX-Trigger: listChanged` so the main list
  refreshes (that is how applied items vanish from everything).
- Existing `POST /i/{id}/action` keeps working for icon actions (done, dismiss, snooze until=1d|3d|1w|YYYY-MM-DD,
  priority delta, reopen). For the compact row: done/dismiss/snooze respond with a one-line stub "Done · Undo" (Undo posts
  reopen and gets the row back); priority responds with the re-rendered row.
- Keep `/b/{slug}`, `/r/{id}`, `/i/{id}`, `/history`, `/search` and the /api endpoints working. The bot page and item page
  should use the new compact row partial too (item page may additionally show the full action history list).
- Local-only API additions (with _verify_local_only): `GET /api/actions?status=submitted[&bot=x]`, `POST /api/actions/{id}/mark`
  JSON {status:"processing"}; `/api/actions/{id}/ack` accepts discuss and `keep_open`.
- Banner (in base or main view, small, one line, muted warning style) shown when `dispatch_configured()` is False:
  "Processing trigger not configured yet; submitted items will be picked up on the bots' next runs"

## 4. Filters (main view)
Filters: Bot, Area, Status, NEW only, Has due date. Apply immediately on change: the filter form gets
`hx-get="/" hx-trigger="change" hx-target="#item-list" hx-swap="outerHTML" hx-push-url="true"` (the list partial's root has
id="item-list"; it must also have `hx-get` with the current query and `hx-trigger="listChanged from:body"` so it can refresh in
place keeping filters). No Apply button. Full page loads with a query string must render with the filters applied and
the controls preselected.
- Bot options: "All bots" then ALL 12 bots in config order, value = cli_name: chief-of-staff, radar, zero, family, money,
  business, chess, pika, friends, project-bot, one-offs, coding (label = bot name). Resolve with find_bot.
- Area options: All areas + distinct non-null items.area values in the DB (sorted). Filter by items.area.
- Status: open (default: not done/dismissed/resolved and not currently snoozed), snoozed (snooze_until > today),
  closed (done/dismissed/resolved), all.
- NEW only: latest report_item flags contain NEW. Has due date: items.due_at not null.
- Filters must apply to EVERY section of the list including the Chief of Staff section (bot=chief-of-staff shows only CoS;
  bot=zero hides CoS). Today the filters appear to do nothing — find out why and fix it. Each filter must provably narrow
  the results (tests).

## 5. Compact UI (templates + style.css). The operator uses desktop Chrome in DARK MODE; also must work on a phone.
- Remove the bot tab/chip bar from base.html (`bot-chips-bar`). Header: one slim line: "Bot Briefs", Today, History,
  search box, and on the right the Submit all bar.
- Container max-width ~1600px on desktop (width: 100%, small side padding), stays fluid on mobile.
- Much tighter spacing: base font 14px, line-height ~1.3, card/section padding 4-8px, row padding ~3px 6px, small gaps.
  Sections (Chief of Staff, then each bot) are compact headers ("Radar · 12 open · report 2026-10-23 ↗ sheet") followed by rows.
  Bots with zero rows after filtering are hidden.
- One-line item row (partials/item_row.html, used everywhere), flex, no wrapping on desktop where possible:
  `#display_id` · title (bold-ish, truncates with ellipsis) · short detail (muted, truncated with ellipsis on one line;
  clicking the row text toggles the full detail/markdown expanded below, pure CSS/`<details>` or tiny JS) · links as small
  🔗 icons (title attribute = label, target=_blank) · priority badge P1/P2/P3 if set · NEW badge · due date badge · icon
  buttons: ✓ done, ✕ dismiss, ⏰ snooze menu (a small <details> dropdown with 1d / 3d / 1w / date input), ▲ priority up,
  ▼ priority down (each with title/aria-label, ~24px square) · single-line text input (placeholder "Reply…", ~220px,
  grows to a multi-line textarea-like wider box on focus via CSS; use a <textarea rows=1> styled single-line that expands on
  focus) · Send button · Queue button. Sub-items (parent_display_id set) are indented slightly.
  On narrow screens (<700px) the row wraps into two lines: text on line 1; icons + input + buttons on line 2; touch targets
  at least 32px.
- Drafts: tiny inline JS in base.html (no frameworks): every reply box has `data-item-id`; on input save to
  localStorage key `briefs:draft:<id>` (remove when empty); on page load and after every htmx swap (htmx:afterSwap /
  htmx:load) restore values into boxes; listen for the `draftClear` and `draftsSubmitted` events (from HX-Trigger) to delete
  keys; keep the Submit all count = data-queued + number of non-empty draft keys (all keys in localStorage, not just
  visible ones, so drafts survive filter changes). Submit all button: hx-post="/submit-all" with hx-vals computed from ALL
  localStorage drafts (`js:` hx-vals or an htmx:configRequest listener), hx-confirm not needed. Send/Queue buttons post the
  row's textarea (hx-include) and must not submit an empty box (disable when empty or return 400).
- Dark mode: keep/extend the existing `prefers-color-scheme: dark` variables so every new element looks right in dark mode.
- The old big action panel (partials/item_actions.html) is replaced by the inline icons; keep an action-history list on the
  item page (statuses incl. queued/submitted/processing/discuss with bot notes).

## 6. CLI (cli.py)
- `brief actions --status <status> [--bot X] [--json]` → list_actions (all bots, ordered by rank). Keep the existing
  `brief actions --item <id>` working (make --item optional; require one of --item/--status).
- `brief mark <id> [<id>...] processing`.
- `brief ack <id...> --status applied|failed|discuss --note "..." [--keep-open]`.
- `brief inbox --bot X [--json] [--include-seen] [--peek]` per section 1.
- `brief submit` is NOT needed.

## 7. Tests (tests/test_m25.py; use temp DBs and the TestClient with headers
`Tailscale-User-Login: <USER_EMAIL>`, `HX-Request: true`, `Origin: http://testserver` as needed)
- migration from an M2-shaped DB (old CHECK) keeps rows and allows 'queued'; idempotent.
- each filter narrows: build a DB with items across several bots/areas/NEW/due/snoozed/done; assert bot=zero shows only
  Zero rows, bot=chief-of-staff only CoS rows, area, status=snoozed/closed/all, new, due. Also assert all 12 bots in the
  Bot <select>. Assert the HX list partial path returns `id="item-list"` and no full page.
- Send: creates submitted action with rank, removes row, HX-Trigger contains inprogressChanged; stub notify called once with
  chief-of-staff and "1 action submitted"; wake_status recorded; not-configured path records 'dispatch not configured' and
  the banner shows.
- Queue: creates queued action; row shows chip; submit-bar data-queued increments.
- Submit all ordering: CoS item at position 2 ranks before a non-CoS P1 item; P1 before P3; due sooner first; NEW first;
  older first; queued actions included; one dispatch call with the right count; empty → 400.
- Lifecycle: mark processing; ack discuss keeps item open and in In progress with the note; ack applied resolves the item
  (status done) and it leaves the main list and In progress; --keep-open keeps it; inbox flips queued/submitted → processing
  and pending → seen; peek doesn't.
- In progress visibility: GET /in-progress empty → placeholder without "every 15s"; with a submitted action → contains
  "every 15s" and the item; ids param mismatch → HX-Trigger listChanged.
- CSRF: /i/{id}/send, /i/{id}/queue, /submit-all reject missing HX-Request or bad Origin (403); login gate 403 for other
  users.
- CLI: actions --status submitted --json ordering by rank, mark, ack --status discuss, ack --keep-open.

When done, print a concise summary of files changed, design decisions, and the final pytest result line.
