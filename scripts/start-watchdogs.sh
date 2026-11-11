#!/usr/bin/env bash
# Phase 11: check monit config, start monit + guardian, show summary.
monit -c ~/.config/monit/monitrc -t && /home/box/agent-data/shared/watchdogs/bin/watchdog-ensure
sleep 5; monit -c ~/.config/monit/monitrc summary
