# Task: build "Bot Briefs" dashboard (M0 importer + M1 CLI/UI) in THIS directory

You are working in /home/box/agent-data/shared/dashboard (a git repo). ONLY create/modify files inside this directory.
Do NOT read or write anything outside it. Do NOT start long-running servers in the background and leave them; you may run
short tests. A Python venv already exists at .venv (fastapi, uvicorn, jinja2, markdown-it-py, python-multipart, pytest, httpx
installed; use `.venv/bin/python`). htmx is vendored at app/static/htmx.min.js. No network access is needed; don't pip install
anything else. Do not use any CDN — everything must be served locally.

Real sample inputs (read-only, personal data, gitignored) are in dev/samples/:
- radar-2026-10-2{2,3}.md         Radar bot markdown briefs
- zero-2026-10-2{2,3}.md          Zero (email triage) markdown briefs
- zero-reported.json              Zero state: {"updatedAt","schema","items":[{key,title,firstSeen,state,priority,deadline,resolved,status,lastReported,threadId?}],...}
- family-state.json               Family state: open_items[{item,priority,first_reported,acted,source:"Gmail thread <id>",resolved}], reply_sheets.current{name,id,rows}
- cos-reqs.json                   Chief of Staff: a Google Sheets batchUpdate request list; the updateCells rows hold
                                  columns ID | Item | Status / details | Your reply | Links (Links cell is a =HYPERLINK("url","label") formula or string)
- transcripts-sendtouser.json     list of {agent (bot uuid), name (bot name), ts (ISO local time of the turn), body (markdown sent to user)}
Look at them before writing parsers. Brief markdown varies by bot. Examples of item lines:
  `1. [Hyades](https://...): text`      `1. **Checking *<ACCOUNT_LAST4> has been below...** more`     `1. Sunday Oct 25`
  `   - 1.1 [SIPB/hyades#3](url): text`  `    - 1.1 text`   `   1.1. text`   `   - 1.2a text`
  Section headings like `**Needs you (ranked)**`, `**Action needed**`, `**Coming up (optional)**` precede items.
  Title is usually the first bold line, e.g. `**Radar weekly review, Fri Oct 23** (covers ...)`.
  Reply sheet link: a markdown link to docs.google.com/spreadsheets whose label often is `<Bot> replies YYYY-MM-DD` or `<Bot> weekly YYYY-MM-DD`.
  Flags appear as words in the item line: `(NEW)`, `NEW:`, `(new)`, `(updated)`, `resolved`, `done`.

## Stack / layout
- Python 3.12, FastAPI + Jinja2 templates + htmx, SQLite (WAL, FTS5) via stdlib sqlite3. No ORM. No JS build. Small hand-written CSS.
- Package `briefs/` (importable library) with:
  - `config.py`: DB path default `data/briefs.db` relative to repo root, overridable with env BRIEFS_DB. PORT default 8642 (env BRIEFS_PORT).
    ALLOWED_LOGINS default {"<USER_EMAIL>"} (env BRIEFS_ALLOWED_LOGINS comma list). BOTS registry (below).
  - `db.py`: connect() (WAL, foreign keys, row_factory), init_schema() idempotent (CREATE IF NOT EXISTS), migrations-safe.
  - `parse.py`: markdown brief -> structured items; link extraction; item key; flag detection; title/date/kind/sheet-url detection.
  - `store.py`: upsert_report(...) used by BOTH importer and CLI publish (single code path), item matching/carry-forward, queries.
  - `importer.py`: source adapters + `run_import(sources: dict, since: date)`; CLI entry `python -m briefs.importer [--since YYYY-MM-DD] [--dry-run]`.
  - `web.py`: FastAPI app (`app = create_app()`), auth middleware, routes, templates.
  - `cli.py`: the `brief` CLI (argparse).
- Templates in briefs/templates/, static in briefs/static/ (MOVE app/static/htmx.min.js there; delete the empty app/ dir).
- tests/ with pytest tests using a temp DB and the dev/samples files (skip sample-based tests if samples missing).
- README.md (see end).

## Bots registry (id, slug, name, sort order). Chief of Staff is special (its list goes on top of Today).
00000000-0000-4000-8000-000000000001 cos "Chief of Staff"
00000000-0000-4000-8000-000000000002 radar "Radar"
00000000-0000-4000-8000-000000000003 zero "Zero"
00000000-0000-4000-8000-000000000004 family "Family"
00000000-0000-4000-8000-000000000005 money "Money"
00000000-0000-4000-8000-000000000006 business "Business"
00000000-0000-4000-8000-000000000007 chess "Chess"
00000000-0000-4000-8000-000000000008 pika "Pika"
00000000-0000-4000-8000-000000000009 friends "Friends"
00000000-0000-4000-8000-000000000010 project "Project"
00000000-0000-4000-8000-000000000011 oneoffs "One-offs"
00000000-0000-4000-8000-000000000012 coding "Coding"
Bots may be referred to by slug, name (case-insensitive) or uuid everywhere.

## Schema (exactly these tables; add indexes as needed)
bots(id TEXT PK uuid, slug UNIQUE, name, sort INT, area_default TEXT)
reports(id INTEGER PK, bot_id FK, kind TEXT CHECK in ('daily','weekly','cos','adhoc'), report_date TEXT 'YYYY-MM-DD',
        title, body_md, sheet_url, source TEXT ('file:/path', 'transcript', 'cli', 'state:/path'), source_ref TEXT,
        content_hash TEXT, created_at, updated_at, UNIQUE(bot_id, report_date, kind))
items(id INTEGER PK, bot_id FK, item_key TEXT, title, status TEXT CHECK in ('open','done','snoozed','dismissed','resolved') DEFAULT 'open',
      priority INT NULL, area TEXT NULL, due_at TEXT NULL, snooze_until TEXT NULL, first_seen TEXT, last_seen TEXT,
      links_json TEXT DEFAULT '[]', meta_json TEXT DEFAULT '{}', UNIQUE(bot_id, item_key))
report_items(id INTEGER PK, report_id FK ON DELETE CASCADE, item_id FK, display_id TEXT ('1','1.2','1.2a'), parent_display_id TEXT NULL,
      position INT, section TEXT NULL, body_md TEXT, flags TEXT (comma list of NEW/updated/resolved), UNIQUE(report_id, display_id))
actions(id INTEGER PK, item_id FK NULL, report_id FK NULL, type TEXT, payload_json TEXT, author TEXT, status TEXT DEFAULT 'pending'
      CHECK in ('pending','seen','applied','failed'), bot_note TEXT, created_at, updated_at)   -- created now, UNUSED until M2
FTS5: reports_fts(title, body_md, content='reports', content_rowid='id') and items_fts(title, body, content='' or external)
      kept in sync (triggers or explicit refresh in upsert). Search must return both reports and items with snippets.

## Parsing rules (parse.py) — must be robust and well unit-tested against the samples
- parse_brief(md) -> {title, sheet_url, sheet_name, date_hint, kind_hint, items:[{display_id, parent, position, section, text_md,
  title (plain text of first sentence/clause, links stripped, <=160 chars), links:[{label,url}], flags:set}]}
- Top-level item: line matching ^\s{0,1}(\d+)\.\s+ ; sub-item: ^\s{2,}(?:[-*]\s+)?(\d+\.\d+[a-z]?)\.?\s+ . Continuation lines
  (indented, non-item, non-blank) append to the current item. A blank line does not end an item but a non-indented paragraph,
  heading or bold section line does. Bold section lines (`**...**` alone on a line) set `section` for following items.
- Lines that are not items (intro paragraphs, bullet lists of "at a glance") remain only in body_md.
- Links: markdown [label](url) plus bare https?:// URLs. Reply-sheet links (docs.google.com/spreadsheets) are "sheet" links,
  not source links.
- item_key (stable across days; NEVER uses display number):
  1) first source URL in the item's own text, normalized (strip fragments/query except for Gmail ids, trailing slashes);
     preferred hosts: github.com, forgejo.*, *.atlassian.net/jira, mail.google.com / dodosend.com /
     dodo-link.pages.dev / dodosend://thread/<id> (use the thread id: key "gmail:<id>"),
     gerrit.*, any other non-sheet URL. Displayed thread links must be
     https://dodosend.com/t/<16hex>?account=<email> (account from host config); do not show
     dodosend:// or mail.google.com thread/fallback links. Gmail search links may stay
     mail.google.com only when no thread id is known.
  2) a Gmail thread id written in text ("thread 0123456789abcdef" / "threadId") -> "gmail:<id>";
  3) else "h:" + sha1(normalized title)[:12] where normalized = lowercase, links→labels, strip flags words (new, updated,
     resolved, still, now), strip dates/times/numbers, punctuation, collapse spaces, first 12 words.
- Carry-forward matching in store.py: when an item's key is an "h:" hash and no existing item has it, compare its normalized
  title against this bot's items seen in the previous 10 days (difflib ratio >= 0.72, prefer same parent display id/section);
  if matched, reuse that item (record alias in items.meta_json.aliases). This keeps per-item timelines continuous.
  Sub-items are their own items (keyed by their own URL/hash; their parent is linked via report_items.parent_display_id).
- flags: NEW if /\bNEW\b/ or "(new)" or "new:" at start; updated if "(updated)"; resolved if "(resolved)" / "resolved" / "✅" / "done" at line start.
- date detection: from reply sheet name `... YYYY-MM-DD`; else from title like "Fri Oct 23" (year 2026 default/current year);
  else caller-supplied fallback. kind: 'weekly' if "weekly" in title or sheet name, else 'daily'; CoS always 'cos'.

## store.upsert_report(conn, bot, date, kind, title, body_md, sheet_url, source, source_ref, items=None)
- If items is None, parse from body_md. Idempotent: same (bot,date,kind) replaces that report's report_items (delete+insert)
  and updates the report row only if content_hash changed; returns (report_id, created|updated|unchanged, n_items).
- Updates items: first_seen = min, last_seen = max(report_date), title = latest, links merged (dedupe by url), status:
  if flags contain resolved → 'resolved'; if a report re-mentions an item → keep status unless it was 'resolved'/'done' and now
  appears without resolved flag → reopen to 'open' (record in meta). Never touch items with status 'dismissed'/'snoozed' from
  user (M2) except last_seen.
- After processing all reports of a bot for a date that is that bot's latest date: items of that bot that are 'open' and not
  mentioned in the bot's latest report stay 'open' but get meta stale=true (UI shows "not in latest report"). Don't auto-close.
- Also expose: items_open(bot=None, include_all=False), today_view(), report_history(bot), item_timeline(item_id), search(q).

## Importer sources (importer.py). Read-only: NEVER write to source paths. Rerunnable. Default since = today-14d.
Source paths are configurable via a dict (defaults below) so tests can point at dev/samples:
- radar_md_glob: /workspace/radar-????-??-??.md  (date from filename)
- zero_md_glob: /workspace/triage-state/reports/????-??-??.md (date from filename)
- zero_state: /workspace/triage-state/reported.json → enrich Zero items: fuzzy-match (difflib >=0.55 on normalized text) each
  Zero report item title to reported.json items; if matched, use item_key "zero:<key>" (or "gmail:<threadId>" if threadId),
  set priority (high=1, low=3), due_at=deadline, status resolved if resolved true, first_seen=firstSeen.
  Also create items for reported.json entries not in any report (status from resolved) with first_seen=firstSeen, last_seen=lastReported,
  only if lastReported >= since.
- family_state: /home/box/agent-data/family-digest/state.json → same enrichment for Family items using open_items
  (source "Gmail thread <id>" → key "gmail:<id>", priority, first_seen=first_reported, resolved→status resolved with note in meta).
  Family report items whose text matches (difflib >=0.55) get that key.
- cos_reqs: /workspace/cos/reqs.json → ONE CoS report (kind 'cos') with items from the updateCells rows (skip header row);
  ID column = display_id, parent = ID minus last ".segment", Item = title, Status/details = body, Links from HYPERLINK formula(s)
  (there may be multiple; parse =HYPERLINK("url","label") and plain URLs). report_date = file mtime date (local tz America/New_York)
  unless the sheet title/first rows contain a date. body_md is synthesized as a markdown numbered list from the rows.
- transcripts: data/sources/transcripts-sendtouser.json (a snapshot I will place there; list of {agent,name,ts,body}) →
  for each bot, candidate briefs = messages whose body contains a numbered item list (a line ^\d+\.\s) AND (a docs.google.com/spreadsheets
  link OR a bold first line containing "daily"/"weekly"/"review"/"roundup"/"brief"). Ignore candidates shorter than 300 chars.
  Date = parse_brief date_hint, else ts date if ts hour < 12 else skip. If several candidates map to the same (bot,date,kind), keep
  the one with the latest ts. Files (radar/zero md) win over transcripts for the same (bot,date,kind).
- Return/print a summary table: per bot → reports created/updated/unchanged, items total/open, date range.

## brief CLI (cli.py; invoked via `brief` wrapper, also `python -m briefs.cli`)
- `brief publish --bot <slug|name|uuid> [--date YYYY-MM-DD (default today America/New_York)] [--kind daily|weekly|cos|adhoc]
   [--title T] [--sheet-url URL] [--file PATH | stdin] [--items-json PATH]`  → writes via store.upsert_report directly to the DB
   (works even if the web server is down). --items-json accepts a list of rows [[ID, Item, Status/details, [links...]], ...] where a
   link is a URL string or [label,url] / {"label","url"}; when given, items come from it and the markdown is kept as body. Prints
   JSON {report_id, status, items, url:"<base>/r/<id>"} where base from env BRIEFS_BASE_URL (default http://127.0.0.1:8642).
- `brief items [--bot B] [--open] [--all] [--json]` : --open (default) lists open items across bots (or one bot) with
   display id from latest report, title, first_seen, last_seen, status, key; --all includes all statuses. Human table by default.
- `brief import [--since D] [--dry-run]` runs the importer. `brief init` creates the schema. `brief serve` runs uvicorn on 127.0.0.1:PORT.
- `brief search QUERY`.

## Web app (web.py) — mobile-first, clean, fast, server-rendered; htmx for filters/search without full reloads
- Bind ONLY 127.0.0.1 (served privately via Tailscale Serve which injects identity headers).
- Auth middleware (all routes incl. static except /healthz):
  login = request.headers.get("Tailscale-User-Login"). Log every request's seen login (or "-") + path + client host to
  logs/access.log (create dir) and to the python logger, one line each, at INFO.
  If login present: allow only if in ALLOWED_LOGINS else 403 page ("Not allowed for <login>").
  If absent: allow only if client host is 127.0.0.1/::1 AND the request has NO X-Forwarded-For / Tailscale-* headers
  ... BUT NOTE: tailscale serve proxies from 127.0.0.1 and adds X-Forwarded-For; requests proxied by serve from untagged
  tailnet users carry Tailscale-User-Login. So: proxied (X-Forwarded-For or X-Forwarded-Host present) without login → 403.
  Direct local (no forwarding headers) → allowed (for CLI/curl/health checks). Everything else → 403.
  /healthz returns "ok" unauthenticated (no data).
- Show the logged-in user's name (Tailscale-User-Name) in the header if present.
- Routes:
  /             Today: top = Chief of Staff latest list (collapsible hierarchical items with links, "as of" date); below, for each bot
                (sorted), its latest report date + title + open items (display ids from the latest report), NEW/updated badges,
                due chips, "stale" marker. Bots whose latest report isn't today get a muted "last report <date>" label.
                Filter bar (htmx GET, hx-push-url): bot (chips), area, "NEW only", "has due date", status (open default / all).
  /b/{slug}     Bot tab: latest report rendered (full markdown → HTML with links, target=_blank rel=noopener), then items list
                with filters, then report history (dates list → /r/{id}).
  /r/{id}       A report: title, date, kind, sheet link, rendered markdown body; items with links to their timelines;
                prev/next report of same bot.
  /i/{id}       Item timeline: title, status, key, links, first/last seen, then each day it appeared (date, bot report link,
                display id, flags, body as rendered markdown), newest first.
  /history      All reports by date (grouped by date, newest first), filter by bot.
  /search?q=    FTS search over reports + items with highlighted snippets (bm25 order); htmx live search (debounced 300ms).
  /api/items, /api/reports?bot=&date=, /api/report/{id}  JSON (same auth).
- Top nav: Today · tabs per bot (horizontal scroll chips on mobile) · History · Search box.
- Markdown rendering with markdown-it-py (commonmark + linkify + tables), html disabled (escape raw HTML), links open in new tab.
- Design: system font, max-width 900px, light/dark via prefers-color-scheme, big tap targets (44px), sticky top bar, cards per bot,
  muted grays, one accent color, badges for NEW (accent), updated (amber), resolved (green, strike), due (red if past/today).
  Must look good at 390px wide. No external fonts/CDNs.
- Times shown in America/New_York with "ET".

## Tests (pytest, must pass): parse.py on each sample (item counts > 0, display ids, keys stable when display number changes,
reply-sheet link detected, CoS rows parsed with links), store idempotency (import twice → second run all unchanged, same counts),
carry-forward matching, CLI publish + items --open, web auth: 403 for proxied-without-login, 403 for wrong login, 200 for
<USER_EMAIL>, 200 for direct local, /healthz ok; pages /, /b/radar, /r/1, /i/1, /history, /search?q=hyades render 200.

## README.md
Purpose; architecture; data model (tables+columns, item_key rules, carry-forward); sources and how the importer works and how to
rerun it; CLI usage with examples for bots (`brief publish --bot money --file brief.md --sheet-url ... [--items-json rows.json]`;
`brief items --open --bot money --json`); web routes; auth model (Tailscale Serve identity headers, localhost exception, never Funnel);
run/keepalive: leave a section "Keeping it running" with placeholder text "See ops/ (filled in by operator)"; M2 plan (actions table).

When done: run the full test suite and `BRIEFS_DB=/tmp/briefs-test.db .venv/bin/python -m briefs.importer --since 2026-10-09`
with sources pointed at dev/samples (add an env BRIEFS_SOURCES_DIR=dev/samples mode that maps the defaults to the sample files:
radar-*.md, zero-????-??-??.md, zero-reported.json, family-state.json, cos-reqs.json, transcripts-sendtouser.json),
fix failures, and finish with a short summary of files created and test results. Commit nothing (operator will commit).
