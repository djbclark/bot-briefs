# Phase 2: Bot Briefs dashboard (FastAPI + SQLite + htmx)

[Back to README](../README.md) | Previous: [phase-01-box-layout.md](phase-01-box-layout.md) | Next: [phase-03-brief-cli.md](phase-03-brief-cli.md)

## How the CoS runs this phase

1. Explain in 2-4 sentences what this phase does and why. Mark what is Grok-Bot-specific vs generic.
2. Show the relevant repo files/examples. Anything marked `EXAMPLE TEMPLATE, DO NOT USE AS-IS`: show as an example, ask the interview questions, draft the user's own version, get an explicit OK.
3. Ask before running anything that installs, overwrites, sends, spends, exposes a service, or is destructive.
4. Do the steps. Prefer `scripts/install.sh --phase 2` (dry run) then `--apply` for file copies.
5. Run the Verify section and report results honestly.
6. Record progress (PROGRESS.md + memory) and wait for the user before the next phase.

## Guide (from the operator's working system)

```text
FastAPI app (Python 3.11+), SQLite with FTS5, Jinja2 + htmx, uvicorn on 127.0.0.1:8642, code in shared/dashboard (git repo).
Full source is in this repo under dashboard/; its README.md is the spec (data model, importer, CLI, routes, auth, M2
actions, section 10 VPS mirror). Modules:
  briefs/config.py   paths, port, BRIEFS_PUBLIC_URL, BRIEFS_ALLOWED_LOGINS, BOTS table (agent id, slug, name, sort, area, cli name)
                     <-- PERSONALIZE (EXAMPLE TEMPLATE): your bots, agent ids (after phase 5), login, tailnet URL
  briefs/db.py schema+migrations | store.py publish/upsert, stable keys, carry-forward, stale | parse.py markdown->items
  importer.py history import (optional) | cli.py `brief` | actions.py item actions + ranking | wake.py BRIEFS_NOTIFY_CMD
  web.py routes (/, /bot/<slug>, /r/<id>, /item/<id>, /search, /history, /cleanup, /healthz, actions) + identity-header auth
  cleanup.py bulk cleanup | syncapi.py + mirror.py VPS mirror (phase 13) | ops/ ensure, brief wrapper, ops/vps units
Auth: listens on 127.0.0.1 only; Tailscale Serve adds Tailscale-User-Login; 403 unless login in BRIEFS_ALLOWED_LOGINS.
/healthz open. /sync/* (VPS) needs a bearer token instead.
Steps: 1) the files listed under 'Repo files' below (`scripts/install.sh --phase N`) 2) edit briefs/config.py 3) scripts/dashboard-venv.sh (venv, deps, brief init,
pytest) 4) scripts/start-dashboard-foreground.sh to test (Ctrl-C); monit runs it later (phase 11).  Verify: `scripts/verify.sh 2`.
Alternative: give README.md + this text to Claude Code via acp-run to regenerate; copying is more reliable.
```

## Notes

Personalize `dashboard/briefs/config.py` (BOTS table, `BRIEFS_PUBLIC_URL`; allowed logins come only from `BRIEFS_ALLOWED_LOGINS`, fail-closed if unset) after copying: its ids, login and URLs are placeholders; set your own in `config.local.env`. `htmx.min.js` is not in the repo; `scripts/dashboard-venv.sh` downloads it (htmx 2.0.4 from unpkg). The dashboard's own [README.md](../dashboard/README.md) is the full spec.

## Repo files and where they go (62 files)

Copy with `scripts/install.sh --phase 2` (dry run; add `--apply` to write; existing files are kept unless `--force`, which makes a `.bak-<timestamp>` first). Manual equivalent: `install -D -m <mode> <repo_path> <destination>`.

| repo path | destination | mode |
|---|---|---|
| `dashboard/` (4 files) | `/home/box/agent-data/shared/dashboard/` | as in [config/manifest.tsv](../config/manifest.tsv) |
| `dashboard/briefs/` (13 files) | `/home/box/agent-data/shared/dashboard/briefs/` | as in [config/manifest.tsv](../config/manifest.tsv) |
| `dashboard/briefs/static/` (3 files) | `/home/box/agent-data/shared/dashboard/briefs/static/` | as in [config/manifest.tsv](../config/manifest.tsv) |
| `dashboard/briefs/templates/` (9 files) | `/home/box/agent-data/shared/dashboard/briefs/templates/` | as in [config/manifest.tsv](../config/manifest.tsv) |
| `dashboard/briefs/templates/partials/` (6 files) | `/home/box/agent-data/shared/dashboard/briefs/templates/partials/` | as in [config/manifest.tsv](../config/manifest.tsv) |
| `dashboard/dev/` (5 files) | `/home/box/agent-data/shared/dashboard/dev/` | as in [config/manifest.tsv](../config/manifest.tsv) |
| `dashboard/docs/` (1 files) | `/home/box/agent-data/shared/dashboard/docs/` | as in [config/manifest.tsv](../config/manifest.tsv) |
| `dashboard/ops/` (3 files) | `/home/box/agent-data/shared/dashboard/ops/` | as in [config/manifest.tsv](../config/manifest.tsv) |
| `dashboard/ops/vps/` (8 files) | `/home/box/agent-data/shared/dashboard/ops/vps/` | as in [config/manifest.tsv](../config/manifest.tsv) |
| `dashboard/tests/` (10 files) | `/home/box/agent-data/shared/dashboard/tests/` | as in [config/manifest.tsv](../config/manifest.tsv) |

## Verify

```bash
scripts/verify.sh 2
```

Done when the checks pass and the user agrees. Then update PROGRESS.md: `phase 2 done <date>; next 3`.
