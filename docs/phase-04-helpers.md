# Phase 4: Helper scripts (ref counter, report-to-items, cos-gather, notify-bot ...)

[Back to README](../README.md) | Previous: [phase-03-brief-cli.md](phase-03-brief-cli.md) | Next: [phase-05-bots.md](phase-05-bots.md)

## How the CoS runs this phase

1. Explain in 2-4 sentences what this phase does and why. Mark what is Grok-Bot-specific vs generic.
2. Show the relevant repo files/examples. Anything marked `EXAMPLE FROM DANIEL, DO NOT USE AS-IS`: show as an example, ask the interview questions, draft the user's own version, get an explicit OK.
3. Ask before running anything that installs, overwrites, sends, spends, exposes a service, or is destructive.
4. Do the steps. Prefer `scripts/install.sh --phase 4` (dry run) then `--apply` for file copies.
5. Run the Verify section and report results honestly.
6. Record progress (PROGRESS.md + memory) and wait for the user before the next phase.

## Guide (from Daniel's working system)

```text
 * bin/ref <BOT>           prints `Ref: <BOT>-<base36>` from ONE global counter (shared/refs/GLOBAL.count, flock, issued.log). Never reset.
 * bin/ref-append <BOT> <f> appends the Ref line to a post file (calls ref once; refuses if one exists).
 * chess-ref, radar-ref    compat shims (exec ref CHESS / RADAR).
 * bin/report-to-items     CoS-shaped markdown -> items.json.
 * bin/cos-gather          stdlib Python: latest daily reports (read-only DB), drops done/dismissed/snoozed, pulls Saner+Shortwave
                           via opencli, scores, NEW flags, resolved_since_yesterday, bots_missing_from_dashboard. PERSONALIZE slugs/sources.
 * ~/.local/bin/notify-bot POST JSON to a bot webhook routine (phase 14); secrets from env or ~/.config/agent-webhook/secrets.env.
 * ~/.local/bin/hermes-ping alert channel used by watchdogs (Daniel: Hermes->Telegram on his Mac). Replace with yours or a no-op.
 * others (job-watch, forgejo helpers, aiuse, tool-update, repo-sync, risk-watch): optional, in local-bin/.
Steps: the files listed under 'Repo files' below (`scripts/install.sh --phase N`), the files listed under 'Repo files' below (`scripts/install.sh --phase N`); `ref TEST` twice (consecutive ids); `notify-bot --list`.
NEVER type a Ref by hand.  Verify: `scripts/verify.sh 4`.
```

## Notes

Ref counter: [bin/ref](../bin/ref), [bin/ref-append](../bin/ref-append). Never type a Ref by hand.

## Repo files and where they go (24 files)

Copy with `scripts/install.sh --phase 4` (dry run; add `--apply` to write; existing files are kept unless `--force`, which makes a `.bak-<timestamp>` first). Manual equivalent: `install -D -m <mode> <repo_path> <destination>`.

| repo path | destination | mode |
|---|---|---|
| [`bin/chess-ref`](../bin/chess-ref) | `/home/box/bin/chess-ref` | 755 |
| [`bin/cos-gather`](../bin/cos-gather) | `/home/box/bin/cos-gather` | 755 |
| [`bin/cos-gather.README.md`](../bin/cos-gather.README.md) | `/home/box/src/one-offs/cos-gather/README.md` | 644 |
| [`bin/radar-ref`](../bin/radar-ref) | `/home/box/bin/radar-ref` | 755 |
| [`bin/ref`](../bin/ref) | `/home/box/bin/ref` | 755 |
| [`bin/ref-append`](../bin/ref-append) | `/home/box/bin/ref-append` | 755 |
| [`bin/report-to-items`](../bin/report-to-items) | `/home/box/bin/report-to-items` | 755 |
| [`local-bin/ai`](../local-bin/ai) | `/home/box/.local/bin/ai` | 755 |
| [`local-bin/aiuse`](../local-bin/aiuse) | `/home/box/.local/bin/aiuse` | 755 |
| [`local-bin/change-hermes`](../local-bin/change-hermes) | `/home/box/.local/bin/change-hermes` | 755 |
| [`local-bin/dashboard-ensure`](../local-bin/dashboard-ensure) | `/home/box/.local/bin/dashboard-ensure` | 755 |
| [`local-bin/forgejo-api`](../local-bin/forgejo-api) | `/home/box/.local/bin/forgejo-api` | 755 |
| [`local-bin/forgejo-mirror-github`](../local-bin/forgejo-mirror-github) | `/home/box/.local/bin/forgejo-mirror-github` | 755 |
| [`local-bin/git-forgejo`](../local-bin/git-forgejo) | `/home/box/.local/bin/git-forgejo` | 755 |
| [`local-bin/hermes-ping`](../local-bin/hermes-ping) | `/home/box/.local/bin/hermes-ping` | 755 |
| [`local-bin/job-watch`](../local-bin/job-watch) | `/home/box/.local/bin/job-watch` | 755 |
| [`local-bin/mac-mcp-ensure`](../local-bin/mac-mcp-ensure) | `/home/box/.local/bin/mac-mcp-ensure` | 755 |
| [`local-bin/mac-mcp-forwarder.py`](../local-bin/mac-mcp-forwarder.py) | `/home/box/.local/bin/mac-mcp-forwarder.py` | 755 |
| [`local-bin/repo-discover`](../local-bin/repo-discover) | `/home/box/.local/bin/repo-discover` | 755 |
| [`local-bin/repo-sync`](../local-bin/repo-sync) | `/home/box/.local/bin/repo-sync` | 755 |
| [`local-bin/risk-watch`](../local-bin/risk-watch) | `/home/box/.local/bin/risk-watch` | 755 |
| [`local-bin/tea-forgejo`](../local-bin/tea-forgejo) | `/home/box/.local/bin/tea-forgejo` | 755 |
| [`local-bin/tg-send`](../local-bin/tg-send) | `/home/box/.local/bin/tg-send` | 755 |
| [`local-bin/tool-update`](../local-bin/tool-update) | `/home/box/.local/bin/tool-update` | 755 |

## Verify

```bash
scripts/verify.sh 4
```

Done when the checks pass and the user agrees. Then update PROGRESS.md: `phase 4 done <date>; next 5`.
