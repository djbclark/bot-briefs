# Phase 17: Keeping a public copy in sync (optional)

[Back to README](../README.md) | Previous: [phase-16-troubleshooting.md](phase-16-troubleshooting.md) | Next: none

Only for people who publish a sanitized copy of their setup. The live box files hold personal values that the repo
copies replace with placeholders, so syncing is always **report, then port by hand**, never a copy.

## Drift tool

`scripts/sync-from-box.sh` compares every `config/manifest.tsv` row with its live destination and prints, without
any file content:

- `DRIFT <repo_path> <n> lines`: the live file changed since the last review (count of differing lines only)
- `MISSING-LIVE <repo_path>` / `MISSING-REPO <repo_path>`
- `NEW <live_path>`: a file in the managed live dirs (watchdogs/bin, watchdogs/conf.d, dashboard, agent-webhook;
  override with `SYNC_NEW_DIRS`) that is not in the manifest. State, db, log, backup/`.bak*`, env and git-ignored files are skipped.

No drift = no output and exit 0, so a routine can skip the model call; drift = exit 3. It never writes to the repo.
Every live file differs from its scrubbed copy, so it remembers what you already reviewed: after porting, run
`scripts/sync-from-box.sh --ack` (stores sha256 of the live files in `~/.local/state/bot-briefs-sync/baseline.tsv`,
outside the repo). `--all` ignores that baseline and shows every difference.

## Weekly review

1. Run `scripts/sync-from-box.sh` (a weekly routine can run it and stay silent on exit 0).
2. For each row, port the *meaning* of the change into a scratch clone of the public repo, scrubbed per
   [SECURITY.md](../SECURITY.md) (placeholders such as `<USER_EMAIL>`, `<tailnet>`, `<AGENT_ID>`). Add new files to the manifest.
3. `scripts/secret-scan.sh .` with your personal deny-list (kept outside the repo); fix every hit.
4. Security review of `git diff` by a read-only agent with no credentials (e.g. `acp-run claude --perm read-only`).
5. Push a branch and open a pull request; the owner reads and merges it. Never push to main from a bot.
6. `scripts/sync-from-box.sh --ack`.

Note: `dashboard/briefs/static/htmx.min.js` is not vendored here; download htmx 2.x into that folder when installing.
