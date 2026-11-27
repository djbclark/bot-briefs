# coding-gather and codex-resets-check

Deterministic helpers for Coding's daily brief (`brief … --bot coding`). Python 3
stdlib only; no model calls. Do **not** create a separate routine for Codex reset
reminders: run these as part of the existing daily brief gather (add
`/home/box/bin/coding-gather` or `/home/box/bin/codex-resets-check` to that
routine's script inputs).

## Codex usage-limit reset credits

`codex-resets-check` reads `~/.local/state/codex-resets/credits.json` and current
Codex usage (`aiuse --json`, `codexbar usage --provider codex --json`, or a
snapshot under `~/.local/state/aiuse/` / `~/.local/state/repo-sync/`). It emits
brief items when:

- a credit expires within 72 hours (expiry shown in America/New_York, labeled ET)
- quota is low and a credit expires within 7 days (drain reminder, so the credit
  can actually be used)
- quota is at least 80% used and an unused credit exists ("good time to use a
  reset"), unless the Codex weekly window resets within 36 hours, in which case
  it suggests waiting for that natural reset

These credits reset the ChatGPT Codex subscription rate limit (`openai/codex`),
not the OpenAI API prepaid balance. Page:
https://chatgpt.com/codex/settings/usage (Usage limit resets).

`credits.json` and real expiry dates are personal. Keep them off git and out of
the public bot-briefs sync. An example schema (fictional dates) is
`examples/codex-resets/credits.json.example`.

    mkdir -p ~/.local/state/codex-resets
    # copy the example, then put the real expiries only in the local state file
    chmod 600 ~/.local/state/codex-resets/credits.json

    codex-resets-check
    codex-resets-check --json --now 2026-11-28T09:00:00-05:00 --no-live --usage-json /tmp/usage.json
    coding-gather --json

Output also lands at `~/.local/state/coding-gather/latest.md` on a live run.
The daily brief should include any `codex-resets:*` keys from that JSON when
publishing (`brief publish --bot coding --items-json …`).

Tests: `codex-resets-check --selftest` runs `tests/test_codex_resets_check.py` when present.
That test file is kept out of this public repo because it holds real credit expiry dates;
write your own with fictional dates.

## aiuse-underpace-check (paid pools under pace, resetting soon)

`bin/aiuse-underpace-check` reads box `aiuse --json` (or `--usage-json FILE`) and flags
paid subscription windows that will reset soon while well under pace, so tokens already
paid for are not lost. `coding-gather` adds its output as the section
"Paid pools under pace, resetting soon (aiuse)" and appends its items (keys
`aiuse-underpace:<provider>:<account>:<window_id>`) to the gather's `items`.

A window is checked when it has `used_percent`, `resets_at` and `window_minutes`, is at
least `min_window_hours` long, resets within `reset_within_hours`, is under 100% used, and
its account has no error, is not operator-gated (`requires_operator_approval`) and its
`billing_kind` is in `include_billing_kinds`. With
`elapsed% = (now - (resets_at - window_minutes)) / window_minutes` and
`projected% = used% / elapsed%`, it is flagged when `projected% < projected_below` or
`elapsed% - used% > under_pace_points`.

| Threshold | Default | Flag |
|---|---|---|
| `reset_within_hours` | 72 | `--reset-within-hours` |
| `projected_below` | 70 | `--projected-below` |
| `under_pace_points` | 30 | `--under-pace-points` |
| `min_window_hours` | 24 | `--min-window-hours` |
| `ignore` | `[]` | config only |
| `include_billing_kinds` | `["subscription_window"]` | config only |

Config: `~/.config/aiuse-underpace/config.toml` (see
`examples/aiuse-underpace/config.toml.example`). Each flagged pool is one item: pool,
% used, % of window elapsed, reset time in ET and a one-line suggestion.

    aiuse-underpace-check                      # live aiuse --json
    aiuse-underpace-check --usage-json /tmp/aiuse.json --now 2026-11-26T07:00:00-05:00 --json
    coding-gather --aiuse-json /tmp/aiuse.json   # gather with a saved snapshot
    coding-gather --no-underpace                 # skip the check

`coding-gather --no-live` skips the check unless `--aiuse-json` is given.
Tests: `python3 -m unittest tests.test_aiuse_underpace_check` or `aiuse-underpace-check --selftest`.
