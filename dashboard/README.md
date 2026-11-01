# Bot Briefs Dashboard (M0 + M1)

A fast, mobile-first daily executive dashboard and cross-bot timeline aggregator for Daniel's autonomous bot swarm.

Bot Briefs ingests reports and transcripts across 12 personal bots (Chief of Staff, Radar, Zero, Family, Money, Business, Chess, Pika, Friends, Techno Euphora, One-offs, and Coding), tracks persistent item lifecycles across days with stable keys and carry-forward fuzzy matching, and renders a clean, server-rendered interface powered by FastAPI, Jinja2, and htmx.

---

## 1. Architecture & Stack

- **Backend**: Python 3.11+, [FastAPI](https://fastapi.tiangolo.com/), standard library [sqlite3](https://docs.python.org/3/library/sqlite3.html) (WAL mode, foreign keys enabled). No ORM.
- **Search**: SQLite FTS5 full-text search with automatic synchronization triggers and highlighted snippets.
- **Frontend**: Server-rendered HTML via Jinja2 templates, styled with a lightweight, responsive CSS system (supporting system font, light/dark themes via `prefers-color-scheme`, and 44px touch targets for mobile).
- **Interactivity**: Vendored [htmx](https://htmx.org/) (served locally from `briefs/static/htmx.min.js`), enabling live debounced search and dynamic filter bar updates without full page reloads.
- **Zero External Dependencies / CDNs**: All static assets and fonts are hosted locally; works entirely offline.
- **Security & Network**: Binds exclusively to `127.0.0.1` and authenticates via Tailscale Serve headers. Never expose via Funnel.

---

## 2. Data Model

The database (`data/briefs.db`, overridable via `BRIEFS_DB`) consists of 5 core relational tables and 2 FTS5 virtual tables:

### Core Tables

- `bots(id TEXT PK, slug UNIQUE, name, sort INT, area_default TEXT)`:
  Registry of bots in priority sort order. Chief of Staff is sort order 0.
- `reports(id INTEGER PK, bot_id FK, kind, report_date, title, body_md, sheet_url, source, source_ref, content_hash, created_at, updated_at, UNIQUE(bot_id, report_date, kind))`:
  Report instances by bot, date, and kind (`daily`, `weekly`, `cos`, `adhoc`). Content changes update `content_hash` and replace associated `report_items`.
- `items(id INTEGER PK, bot_id FK, item_key TEXT, title, status, priority, area, due_at, snooze_until, first_seen, last_seen, links_json, meta_json, UNIQUE(bot_id, item_key))`:
  Canonical items tracked across days. Status is one of `open`, `done`, `snoozed`, `dismissed`, `resolved`.
- `report_items(id INTEGER PK, report_id FK ON DELETE CASCADE, item_id FK, display_id, parent_display_id, position, section, body_md, flags, UNIQUE(report_id, display_id))`:
  Join table connecting a report to its items with hierarchical numbers (`1`, `1.1`), sections, and flags (`NEW`, `updated`, `resolved`).
- `actions(id INTEGER PK, item_id FK, report_id FK, type, payload_json, author, status, bot_note, created_at, updated_at)`:
  Action queue for user feedback (created in M0/M1; used in M2).

### FTS5 Virtual Tables

- `reports_fts(title, body_md, content='reports', content_rowid='id')`:
  Synced automatically with `reports` table via `INSERT`, `UPDATE`, and `DELETE` database triggers.
- `items_fts(title, body, item_id UNINDEXED)`:
  Synced on upsert to enable fast snippet-backed search on item titles and details.

### Stable Item Keys (`item_key`)

Display IDs (e.g. `1`, `2.1`) change daily as item lists are re-ranked. To maintain continuity, items are keyed using:
1. **Preferred Source URLs**: First source link in the item matching preferred hosts (`mail.google.com`, `github.com`, `forgejo.*`, `*.atlassian.net/jira`, `gerrit.*`), normalized by stripping query parameters and fragments (except Gmail 16-hex thread IDs and Gerrit change IDs).
2. **Gmail Thread IDs**: Detected in item text (e.g., `thread 0123456789abcdef`) -> `gmail:<id>`.
3. **Normalized Title Hash**: For items without URLs, `h:` + SHA1 hash of the first 12 words of normalized text (lowercased, links converted to labels, flags and dates/times/numbers stripped, punctuation removed).

### Carry-Forward Matching

When an item produces an `h:` hash not present in the database, `store.upsert_report` compares its normalized title against the bot's items seen in the preceding 10 days using `difflib.SequenceMatcher`. If the similarity ratio is $\ge 0.72$, the existing item is reused, linking the timelines together, and the new key is recorded in `meta_json.aliases`.

### Stale Marking

Items that remain `open` but are omitted from a bot's latest report are not closed; they are marked with `meta.stale = true` and rendered with a "not in latest report" badge.

---

## 3. Sources & Importer

The importer (`briefs.importer`) is idempotent and re-runnable without duplicating reports or items.

### Ingestion Adapters

- **Radar briefs**: Glob `/workspace/radar-????-??-??.md` (or sample `radar-*.md`). Date derived from filename.
- **Zero briefs**: Glob `/workspace/triage-state/reports/????-??-??.md` (or sample `zero-*.md`).
- **Zero state enrichment**: `/workspace/triage-state/reported.json` (or sample `zero-reported.json`). Fuzzy-matches Zero items to enrich priorities (`high` $\to 1$, `low` $\to 3$), deadlines (`due_at`), and thread IDs. Unreported items are imported as stand-alone items.
- **Family state enrichment**: `/home/box/agent-data/family-digest/state.json` (or sample `family-state.json`). Fuzzy-matches `open_items` to extract Gmail thread IDs, priorities, and resolved notes.
- **Chief of Staff**: `/workspace/cos/reqs.json` (or sample `cos-reqs.json`). Ingests Google Sheets `updateCells` batchUpdate rows (display ID, item title, status/details, reply, and `=HYPERLINK` formulas) into a synthesized `cos` report.
- **Transcripts**: `data/sources/transcripts-sendtouser.json` (or sample). Extracts candidate numbered briefs matching spreadsheets or report headings, deduplicates per `(bot, date, kind)` keeping the latest turn. Files override transcripts for identical keys.

### Running the Importer

```bash
# Default run (last 14 days)
.venv/bin/python -m briefs.importer

# Import with specific cutoff date
.venv/bin/python -m briefs.importer --since 2026-10-09

# Dry run without committing changes to DB
.venv/bin/python -m briefs.importer --dry-run

# Run against dev samples directory
BRIEFS_SOURCES_DIR=dev/samples BRIEFS_DB=data/briefs.db .venv/bin/python -m briefs.importer --since 2026-10-09
```

---

## 4. CLI Usage (`brief`)

The `brief` executable is available at `ops/brief` (and symlinked at `/home/box/bin/brief`). It can also be run with `.venv/bin/python -m briefs.cli`.

### Initialize Schema
```bash
brief init
```

### Publish a Report (Direct DB Write)
Bots can publish briefs directly to the database even if the web server is offline:

```bash
# Publish from markdown file
brief publish --bot money --date 2026-10-23 --kind daily --file brief.md --sheet-url "https://docs.google.com/spreadsheets/d/.../edit"

# Publish structured JSON rows
brief publish --bot radar --date 2026-10-23 --title "Radar Structured" --items-json rows.json

# Publish from stdin
echo -e "1. [SIPB/hyades#3](https://forgejo.mit.edu/SIPB/hyades/issues/3): rebase upstream" | brief publish --bot radar
```
Outputs JSON:
```json
{"report_id": 1, "status": "created", "items": 1, "url": "http://127.0.0.1:8642/r/1"}
```

### Inspect Items
```bash
# List open items across all bots (human table)
brief items

# Filter by bot
brief items --bot zero

# Include all statuses (open, resolved, etc.)
brief items --bot zero --all

# Output as JSON
brief items --bot money --open --json
```

### Search Reports and Items
```bash
brief search hyades
```

### Start Web Server
```bash
brief serve --port 8642
```

---

## 5. Web Application

### Routes

- `GET /`: **Today View**. Chief of Staff list on top (hierarchical, collapsible, as-of date), followed by sorted bot cards displaying latest report status, open items, badges (`NEW`, `updated`, `resolved`, `due`, `stale`), and reply sheet links. Supports htmx filtering by bot, area, new-only, due date, and status.
- `GET /b/{slug}`: **Bot View**. Latest report rendered into HTML (links open in new tab), followed by open items list and report history table.
- `GET /r/{id}`: **Report View**. Full rendered markdown body, metadata, reply sheet link, prev/next navigation within that bot, and item links to timelines.
- `GET /i/{id}`: **Item Timeline**. Canonical details, status, key, links, first/last seen dates, and reverse-chronological list of daily appearances with rendered markdown.
- `GET /history`: **Report History**. All briefs grouped by date (newest first) with bot filtering.
- `GET /search?q=`: **FTS Search**. Full-text search with highlighted snippets (`<mark>`) across report bodies and item details, featuring debounced htmx live updates.
- `GET /healthz`: **Health Check**. Unauthenticated endpoint returning `ok`.
- `GET /api/items`: JSON list of open or all items.
- `GET /api/reports`: JSON list of reports with optional `bot` and `date` filters.
- `GET /api/report/{id}`: JSON details of a report and its items.

---

## 6. Authentication & Security

- **Tailscale Serve**: The service binds strictly to `127.0.0.1:8642`. External traffic is proxied via Tailscale Serve, which authenticates tailnet users and injects `Tailscale-User-Login` and `Tailscale-User-Name` identity headers.
- **Allowed Logins**: Only logins in `BRIEFS_ALLOWED_LOGINS` (no default: unset means every proxied request is refused) are authorized. Unknown users receive a `403 Forbidden` response.
- **Localhost Exception**: Direct requests from `127.0.0.1` (or test client) without forwarding headers (`X-Forwarded-For`, `X-Forwarded-Host`, `Tailscale-*`) are permitted for CLI tools, curl, and health monitoring. Proxied requests lacking a verified Tailscale login are rejected with 403.
- **Access Logging**: Every request logs the client IP, path, and authenticated login (or `-`) to `logs/access.log` and the standard Python logger at INFO level.
- **Never Tailscale Funnel**: This dashboard contains personal family, email, and financial data and must never be exposed via public Funnel.

---

## 7. Keeping it running

**URL (tailnet only):** https://grok-bot-box.<tailnet>.ts.net/  (Tailscale Serve, HTTPS 443, path `/` → `http://127.0.0.1:8642`). Never Funnel.

This box has no systemd and no cron, so it uses the same pattern as `mac-mcp-ensure`:

| Piece | What it does |
|---|---|
| `ops/dashboard-keepalive.sh` | Single-instance loop (flock on `~/.local/share/briefs-dashboard/keepalive.lock`). Every 20s it GETs `/healthz`, and (re)starts `uvicorn briefs.web:app --host 127.0.0.1 --port 8642` (setsid/nohup, log `logs/server.log`) if it fails. About every 5 min, if `tailscale serve status` has no mapping to `127.0.0.1:8642`, it re-runs `tailscale serve --bg --https=443 http://127.0.0.1:8642`. It never touches funnel and never removes other serve config. Logs over 5 MB get trimmed. |
| `ops/dashboard-ensure` → `~/.local/bin/dashboard-ensure` | Starts the keepalive if its lock is free. Idempotent. |
| `~/.bashrc`, `~/.profile` | Each ends with a line that runs `dashboard-ensure` (backups: `*.bak-dashboard-20261023`). Any agent shell or login after a box restart brings it back. |
| `ops/brief` → `/home/box/bin/brief` | CLI wrapper (venv python, `PYTHONPATH` = repo). |

Common operations:

```bash
dashboard-ensure                                  # start if not running
curl -s 127.0.0.1:8642/healthz                    # -> ok
tail -f ~/.local/share/briefs-dashboard/keepalive.log logs/server.log logs/access.log
pkill -f "uvicorn briefs.web:app"                 # restart (keepalive brings it back within ~20s)
pkill -f dashboard-keepalive.sh; pkill -f "uvicorn briefs.web:app"   # stop completely
TS="tailscale --socket=/home/box/.local/share/tailscale/tailscaled.sock"
$TS serve status                                  # should show / proxy http://127.0.0.1:8642 (tailnet only)
$TS serve --https=443 off                         # remove the mapping (also stop the keepalive, or it re-adds it)
.venv/bin/python -m briefs.importer               # re-import last 14 days (idempotent)
.venv/bin/pytest -q
```

`logs/access.log` records `login=<Tailscale-User-Login>` for every request, so you can confirm who is hitting it.

## How bots publish (going forward)

Prefer `--items-json` with a **stable key per row**. That key is what ties an item together across days (timeline, NEW, carry-forward, and later M2 actions):

```json
[
  ["1", "Reply to Bob", "Waiting since Tue", [["thread", "https://mail.google.com/mail/u/0/#inbox/18c..."]]],
  ["2", "Electric bill", "Due Fri", [], "money:power-bill-2026-10"],
  {"id": "3", "item": "School form", "details": "Sign + return", "key": "family:school-form", "due": "2026-10-30", "links": ["https://..."]}
]
```

Key precedence: explicit `key` (5th list element or dict `key`), then the item's first source link (Gmail, GitHub or Forgejo preferred; sheet links ignored), then a Gmail thread id in the text, then `h:` + a hash of the normalized title. Never the display number. Republishing the same bot+date updates that report in place.

---

## 8. M2: item actions and bot read-back

Bot-facing instructions live in `/home/box/agent-data/shared/bot-dashboard.md` (the single source for bots).

**UI (M2.5 compact layout).** One-line rows (number, title, truncated detail that expands on click, link icons,
priority/NEW/due badges), max-width 1600px, stacked on screens <= 700px. Inline icon buttons per row: Done, Dismiss,
Snooze menu (1d / 3d / 1w / date), Priority up/down (htmx POST `/i/{id}/action` with `from_row=1`; Done/Dismiss/Snooze
swap in an Undo stub). Each row has a one-line reply box (grows on focus) with **Send** (`POST /i/{id}/send`, status
`submitted`, dispatch fires at once) and **Queue** (`POST /i/{id}/queue`, status `queued`). Unsent drafts live in
localStorage (`briefs.drafts`) and survive refreshes and filter changes; cleared on Send/Queue/Submit all.
**Submit all (N)** in the header (`POST /submit-all`, JSON `{drafts:[{item,text}]}`) turns every draft into an action,
promotes every queued action to `submitted`, reranks all submitted actions (CoS position, P1>P2>P3, due soonest,
NEW, age; `briefs.actions.importance_key`) and fires one dispatch. No bot tab bar; filters (Bot with all 12 bots,
Area, Status open/snoozed/closed/all, NEW only, Has due date) apply on change via hx-get into `#item-list` with the
URL pushed. Unfiltered bot sections show 25 rows plus a "Show all N" button. **In progress** (`GET /in-progress`)
appears only when there are queued/submitted/processing/discuss actions; it shows Daniel's text, status, the bot's
latest note and a reply box, and polls every 15s only while non-empty. Items with an in-progress action are hidden
from the main list. An `applied` reply closes its item (`meta.handled_by_action`, Undo via reopen) unless the bot
acks with `--keep-open`. History is at `/i/{id}/actions` and on the item page.

**Lifecycle.** Text actions: `queued` -> `submitted` -> `processing` -> `applied | discuss | failed`. Icon actions:
`pending` -> `seen` -> `applied | failed`. The DB migration (idempotent, in `db.py`) rebuilds the `actions` table
when its CHECK lacks `queued` and adds `rank`.

**CSRF.** POSTs need `HX-Request: true` and an Origin (or Referer) whose host equals the request host
(X-Forwarded-Host or Host), on top of the Tailscale-User-Login gate. Verified through Tailscale Serve.

**Bot read-back (local only).** `brief inbox --bot <cli-name> [--json] [--include-seen] [--peek]` (returns pending,
queued and submitted; marks pending -> seen and queued/submitted -> processing), `brief actions --status submitted
--json [--bot X]` / `--item <id>`, `brief mark <id...> processing`, `brief ack <id...> --status applied|discuss|failed
[--keep-open] --note "..."`. HTTP equivalents, which refuse any request carrying Tailscale/X-Forwarded headers or from a
non-loopback client: `GET /api/inbox?bot=<name>[&peek=1]`, `GET /api/actions?status=..&bot=..`,
`POST /api/actions/{id}/mark`, `POST /api/actions/{id}/ack` (JSON `{status, note, keep_open}`), `GET /api/items/{id}/actions`.

**Dispatch** (`briefs/wake.py`): Send and Submit all run, as a background task,
`notify-bot chief-of-staff -f dashboard -m none -s "Dashboard submit" "<N> action(s) submitted"`, stdout/stderr
discarded (no secrets logged), 30s timeout; `wake_status` = `dispatch sent` / `dispatch failed (exit N)` /
`dispatch not configured`. `notify-bot --list` is cached 60s; while chief-of-staff isn't configured the page shows
"Processing trigger not configured yet; submitted items will be picked up on the bots' next runs". notify-bot reads
`AGENT_WEBHOOK_CHIEF_OF_STAFF_URL` / `_KEY` / optional `_HEADER` from the environment or from
`${AGENT_WEBHOOK_ENV_FILE:-~/.config/agent-webhook/secrets.env}` (chmod 600). Override the command with
`BRIEFS_NOTIFY_CMD` (tests use a stub). There is no per-reply bot wake any more.

**Publishing.** `brief publish --bot <name> [--kind daily|weekly|cos|adhoc] [--title T] --md report.md
[--items-json rows.json | --from-sheet-rows rows.json]`. Keys: explicit `key`, else first link URL, else title hash.
Rows without IDs get their position. Reports published this way (source `cli`) are never overwritten by the file
importer.

**Nightly safety net.** The keepalive runs `python -m briefs.importer` once a day after 03:45 ET
(`$RUN/last-import` holds the date; output in `logs/import.log`). The import is idempotent.

**Testing without touching real data.** Run a second server on another port with `BRIEFS_DB` pointing at a copy
and `BRIEFS_NOTIFY_CMD` at a stub; `dev/q.py "<sql>" <db>` queries a DB.

---

## 10. VPS copy and mirror (dual-run, added 2026-10-31)

A second copy runs on vps-primary (https://vps-primary.<tailnet>.ts.net/, tailnet only) while the box stays the
system of record for all bots. Details, rollback and conflict rules: `/workspace/vps-dashboard-report.md`.
Code: `briefs/syncapi.py` (VPS-only `/sync/*` API, loaded only when `BRIEFS_SYNC_TOKEN_FILE` is set), `briefs/mirror.py`
(box daemon, run by monit as `briefs-mirror`), `ops/vps/` (VPS units, wake wrapper, backup, `pack-app`). `brief inbox|actions`
first pull VPS-made actions (set `BRIEFS_MIRROR_HOOK=0` to disable). Backups of changed files: `*.bak-20261031`.
