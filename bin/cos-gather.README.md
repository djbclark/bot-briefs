# cos-gather

`/home/box/bin/cos-gather` gathers the data for CoS's 04:14 ET high-priority list. It uses Python 3 stdlib only and makes no model calls.

    cos-gather [--date YYYY-MM-DD] [--as-of ISO] [-o FILE] [--no-opencli] [--no-state] [--bots a,b] [--explain] [--top N] [--selftest]

- What it reads: the briefs DB in read-only mode (`file:...?mode=ro`, or `$BRIEFS_DB`), `source-health.json`, and two read-only commands, `opencli saner tasks --status open` and `opencli shortwave todos`. Each has a 60 s timeout. If one fails, that source is marked error in `sources` and the run continues. If health shows `both_routes_failed`, the source is skipped.
- What it writes: stdout, `-o FILE`, and in live mode only `~/.local/state/cos-gather/YYYY-MM-DD.json`.
- Timing: avoid running it live in the 04:58–05:10 ET window, because opencli calls go through the Chrome bridge. `--date`/`--as-of` runs never call opencli.
- Candidates: open items that are attached (via report_items) to each target bot's latest **daily** report created at or before the cutoff. Items are dropped if status is done, dismissed or resolved, if snooze_until is in the future, if the report row is flagged "resolved", or if the row is a section-header parent. Stale `open` items that no bot republishes are ignored on purpose; the DB has hundreds of them.
- NEW: first_seen is the report date and falls within the last day, or the report row has the `new` flag.
- resolved_since_yesterday: the baseline is yesterday's state file if it exists. Otherwise it is reconstructed as-of yesterday 04:14 ET from the DB. Each resolved item gets a reason: either `status=<closed>` or "no longer in latest brief".
- Backtest (`--date`): the cutoff is that day at 04:14 ET. Limits:
  - items.status has no timestamps, so closed status is NOT applied.
  - Titles and due_at are the current values.
  - A report row edited after the cutoff shows its current items.
- Scoring: see WEIGHTS and KW at the top of the script. Points are additive and every point is listed in score_reasons. Deadlines come from due_at, or are inferred from a month-day next to a deadline word in the title (today or later only).
- False-positive risks:
  - "asked", "renewal" and "$… charge" match FYI money lines.
  - "overdue" library notices score as money.
  - "blocked" matches benign text.
- `backtest.py` compares the output to CoS's published daily briefs.
- Tests: `python3 -m unittest test_cos_gather` or `cos-gather --selftest`.
