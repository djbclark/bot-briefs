# Bot Briefs dashboard: bot integration

Daniel reads every bot's report in one place: https://grok-bot-box.<tailnet>.ts.net/ (tailnet only).
He can act on any item there (done, dismiss, snooze, priority up/down, or a free-text reply/instruction). Those
actions wait in your dashboard inbox until you read them. Everything below runs locally on grok-bot-box with
`/home/box/bin/brief`. It needs no credentials and sends nothing to anyone.
If `brief` is not on your PATH, use the full path `/home/box/bin/brief`.
Code, data model and ops: /home/box/agent-data/shared/dashboard/README.md

## Your `--bot` name

| Bot | `--bot` |
|---|---|
| Chief of Staff | `chief-of-staff` |
| Radar | `radar` |
| Zero | `zero` |
| Family | `family` |
| Money | `money` |
| Business | `business` |
| Chess | `chess` |
| Pika | `pika` |
| Friends | `friends` |
| Techno Euphora | `techno-euphora` |
| One-offs | `one-offs` |
| Coding | `coding` |
| Other | `other` |

## BOT INTEGRATION (do this every run)

**1. At the START of each run, read your dashboard inbox:**

    brief inbox --bot <name> --json

This prints a JSON list (often `[]`). Each entry has `id`, `type`, `payload`, `status`, `rank`, `created_at`,
`history` (earlier actions and notes on the same item) and `item` (`item_key`, `title`, `links`, `display_id`, `url`).
Reading it marks icon actions `seen` and Daniel's text actions `processing`, so he sees you have picked them up.
Act on each one under your own rules, exactly as you would a reply in your reply sheet's "Your reply" column.
Example: draft a reply, but never send anything without Daniel's card.
- `done` or `dismiss`: stop reporting that item (match it by `item.item_key`). The dashboard has already hidden it.
- `snooze` (`payload.until` = YYYY-MM-DD): don't report the item again until that date. On or after that date, report it as normal.
- `priority` (`payload.delta`: -1 = more important, +1 = less): reorder or flag it in your next report.
- `reply` (`payload.text`): an instruction or answer Daniel typed about that item. Handle it like a reply-sheet reply.
  Its status is `queued` (he saved it for later) or `submitted` (he pressed Send or "Submit all"). Handle both.
  If `history` holds an earlier `discuss` note from you, this reply is his answer to that question.
- `reopen`: Daniel undid an earlier done, dismiss or snooze, so the item is live again.

Then acknowledge each action with one of three statuses:

    brief ack <id> --status applied --note "drafted reply to Bob, card sent"
    brief ack <id> --status discuss --note "Which invoice: Oct 3 ($412) or Oct 17 ($96)?"
    brief ack <id> --status failed  --note "couldn't find the thread"

- `applied`: done. For a `reply`, this also closes the item on the dashboard (Daniel can Undo). Add `--keep-open`
  if the item should stay on his list (e.g. you drafted something and are waiting on his card).
- `discuss`: you need Daniel's input. Your note is shown to him as a question in the dashboard's "In progress"
  section with a reply box; his answer arrives as a new `reply` action on the same item.
- `failed`: you couldn't do it; say why in the note.

Daniel sees the status and your note on the item, so keep notes to one short line. Several ids at once also work:
`brief ack 12 13 --status applied --note "..."`.
If a run dies after reading the inbox but before acking, the next run can see those actions again with
`brief inbox --bot <name> --json --include-seen` (adds `seen` and `processing` actions not yet acked).
`--peek` reads without changing any status. Other read-only views:
`brief actions --status submitted --json [--bot <name>]` and `brief actions --item <item-id> --json`.
To claim specific actions without reading the whole inbox: `brief mark <id> [<id> ...] processing`.

Action lifecycle (text actions): `queued` -> `submitted` -> `processing` -> `applied` | `discuss` | `failed`.
Icon actions (done, dismiss, snooze, priority, reopen): `pending` -> `seen` -> `applied` | `failed`.

**2. After writing your daily/weekly report and reply sheet, publish it with stable keys:**

    brief publish --bot <name> --kind daily --title "<report title>" --md /path/report.md --from-sheet-rows /path/rows.json
    # or with your own keyed rows:
    brief publish --bot <name> --kind daily --title "..." --md /path/report.md --items-json /path/items.json

- `--from-sheet-rows`: the same rows you build for the reply sheet, as JSON: `[ID, Item, Status/details, Your reply, Links]`
  (or `[ID, Item, Status/details, Links]`). A header row is skipped. Links can be URLs, `[label, url]`,
  `{"label","url"}`, newline-separated URLs, or `=HYPERLINK("url","label")`.
- `--items-json`: lets you set a stable key per row, either as a 5th list element or as a dict:
  `{"id":"3","item":"School form","details":"Sign + return","links":["https://..."],"key":"family:school-form","due":"2026-10-30"}`
- Stable keys, best first: the Gmail thread id (`gmail:<threadId>`), a GitHub/Jira/Forgejo issue or PR URL,
  a calendar event id (`gcal:<eventId>`), or a stable slug you reuse every day (`money:power-bill-oct`).
  Never use the display number (1, 2.1, ...). With no key, the item's first non-sheet link is used, then a hash of its title.
  Hashed titles break if you reword them, so give keys whenever you can.
- `--date` defaults to today (ET). Use `--kind weekly` for weekly reviews. `--sheet-url <url>` links the reply sheet.
  Republishing the same bot, date and kind updates that report in place, so re-running is safe.
- It prints JSON such as `{"report_id": 41, "status": "created", "items": 12, "url": "..."}`.

**3. Dashboard actions and the reply sheet run in parallel.** Treat a dashboard action exactly like a reply-sheet
reply. Keep making reply sheets as you do now.

**4. If `brief` fails** (non-zero exit or an error), carry on with your report as normal and mention the failure in
one line (e.g. "Dashboard: brief publish failed: <error>"). Never let the dashboard block your report.

## Chief of Staff: dashboard dispatch

When Daniel presses Send on one item or "Submit all (N)", the dashboard runs, in the background:

    notify-bot chief-of-staff -f dashboard -m none -s "Dashboard submit" "<N> actions submitted"

(e.g. "1 action submitted", "3 actions submitted"). That is the only webhook the dashboard sends; individual bots
are not woken per reply. Until Chief of Staff's webhook is configured, the dashboard shows "Processing trigger not
configured yet; submitted items will be picked up on the bots' next runs", and each bot handles its own actions
on its next run as in step 1.

When Chief of Staff is woken with that message:
1. `brief actions --status submitted --json` lists every submitted action across all bots, ordered by `rank`
   (Daniel's importance order: CoS position, then P1>P2>P3, due soonest, NEW, age). Each entry's `bot` says
   which bot owns the item.
2. `brief mark <id> [<id> ...] processing` the ones you are taking on now, so Daniel sees them as processing.
3. Handle each in rank order: do it yourself if it's yours or simple; otherwise route it to the owning bot
   under your normal routing rules (that bot will also see it in its own `brief inbox` on its next run).
4. Ack each one you handled (`applied`, `discuss` with a question, or `failed` with a reason).
   Routed actions are acked by the bot that does the work.
