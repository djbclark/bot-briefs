#!/usr/bin/env bash
# Phase 2: venv, deps, htmx download, DB init, tests.
H="${ROOT:-}/home/box/agent-data/shared/dashboard/briefs/static/htmx.min.js"
mkdir -p "$(dirname "$H")"
[ -e "$H" ] || curl -fsSL -o "$H" https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js || echo "download htmx.min.js (htmx 2.x) manually to $H"
set -euo pipefail
cd /home/box/agent-data/shared/dashboard
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
grep -v '^playwright' requirements.txt > /tmp/req-noplaywright.txt
.venv/bin/pip install -q -r /tmp/req-noplaywright.txt
[ -d .git ] || { git init -q && git add -A && git commit -qm "Bot Briefs dashboard from kit" || true; }
/home/box/bin/brief init || PYTHONPATH=. .venv/bin/python -m briefs.cli init
PYTHONPATH=. .venv/bin/python -m pytest -q --ignore=tests/test_browser_filters.py || echo "some tests failed (see phase 16)"
