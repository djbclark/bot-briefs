#!/usr/bin/env bash
# Phase 2: first test run (Ctrl-C stops). monit runs it later (phase 11).
set -euo pipefail
cd /home/box/agent-data/shared/dashboard && PYTHONPATH=. .venv/bin/python -m uvicorn briefs.web:app --host 127.0.0.1 --port 8642
