<!-- Report Claude wrote after building the VPS mirror (reference for phase 13). EXAMPLE FROM DANIEL, DO NOT USE AS-IS: contains Daniel-specific hosts/paths; adapt. -->

# Bot Briefs dashboard on vps-primary (dual-run) — report, 2026-10-31

Status: running in parallel with the box copy. No cutover. The box dashboard's behaviour is unchanged.

## What runs where
| | URL / port | Service | Paths |
|---|---|---|---|
| Box (unchanged) | https://grok-bot-box.<tailnet>.ts.net/ → 127.0.0.1:8642 | monit `dashboard` | /home/box/agent-data/shared/dashboard |
| **VPS (new)** | https://vps-primary.<tailnet>.ts.net/ → 127.0.0.1:8642, tailnet only (Serve, no Funnel). The existing :7263 Serve mapping is untouched. | `briefs-web.service` | app /var/lib/briefs/app, venv /var/lib/briefs/venv, DB /var/lib/briefs/data/briefs.db, secrets /var/lib/briefs/secrets (mode 600), logs /var/lib/briefs/logs |
| **Box mirror (new)** | none (outbound only) | monit `briefs-mirror` (+ file check `briefs-mirror-status`) | code briefs/mirror.py, state ~/.local/share/briefs-mirror/, status+lag log /home/box/agent-data/shared/briefs-mirror/{status.json,lag.log}, token ~/.config/briefs-mirror/token (600) |

How the VPS is run: vps-primary is NixOS configured from git (github:djbclark/site-djbclark, branch deploy-vps-primary; /etc/nixos only has a README and /etc/systemd/system is read-only). I did not touch the config or deploy anything. The services are **systemd user units of root** in /root/.config/systemd/user/ (briefs-web.service, briefs-wake-retry.{service,timer}, briefs-backup.{service,timer}), enabled with `loginctl enable-linger root` so they start at boot. Restart=always. The Python venv was built with `uv` from system python (only fastapi, uvicorn, jinja2, markdown-it-py, python-multipart, pydantic). Nothing else on the VPS was changed. No config file was edited, so there was nothing to back up there; the pre-flake files in /root/etc-nixos-pre-flake are untouched.

## How it works
**Box → VPS (mirror).** `briefs.mirror run` (every 10 s) reads the box DB read-only, hashes every row of bots/reports/items/report_items/actions and compares with what the VPS last acknowledged (sidecar state.db). Differences are POSTed to `/sync/push` over the tailnet (curl via the box SOCKS proxy), and a row counts as sent only after the VPS answers 2xx. So a freeze, crash or VPS outage just means the same diff is sent later; the VPS upserts by id, so replays never duplicate. It watches the DB, not the `brief` commands, so publish, ack, mark, importer, web actions and cleanup are all covered with no change to the 13 bots. A heartbeat goes every 60 s. A mass-delete guard refuses to mirror >50% deletions. Initial backfill was done with the SQLite backup API (snapshot, integrity-checked, shipped to the VPS); the mirror then sent only changes made after the snapshot.

**VPS → box (Daniel's actions on the VPS UI).** Actions made on the VPS get ids ≥ 1,000,000,000 and are tracked in a VPS-only table. The box daemon polls `/sync/actions` every 10 s (and `brief inbox` / `brief actions` do a short best-effort pull first, 5 s timeout, skipped for 30 s if the VPS is unreachable). Each action is written to the box DB once, in the same transaction as a row in the additive box table `mirror_vps_actions` (vps_id → box_id), with the same item effect as the normal UI path (done/dismiss/snooze/priority/reopen). The box then acks and the VPS renumbers the action to the box id. `brief inbox`, `actions`, `mark` and `ack` work unchanged. Box state changes (processing/applied/discuss/failed, item status) flow back via the mirror and show on both dashboards.

**Conflict rule.** Box is authoritative. Rows pushed from the box overwrite the VPS copy. Exceptions: (1) an item's status/snooze/priority on the VPS is kept while a VPS action for it is still waiting to be pulled; (2) VPS-made actions can still be edited from the VPS (queued → submitted) only while their box status is queued/submitted/pending; once a bot marks or acks them, the box value wins; (3) the VPS keeps its own `wake_status`.

**Wake (VPS → Chief of Staff).** Send / Submit all on the VPS runs `ops/vps/notify-bot-vps` (set as BRIEFS_NOTIFY_CMD), the VPS stand-in for notify-bot. Same payload and same chief-of-staff webhook, using the same settings copied (not printed) from the box's AGENT_WEBHOOK_CHIEF_OF_STAFF_* into /var/lib/briefs/secrets/webhook.env (600). It waits up to 14 s for the box to pull the actions, then POSTs once. On failure it spools the wake (exit 75) and a timer retries every minute with exponential backoff (1→30 min, newest wake only, gives up after 24 h). The actions are already saved in the VPS DB either way. The VPS can reach the webhook host (credential-less GET got HTTP 404, which does not trigger anything).

**Health.** The VPS shows a red banner when no mirror update (including heartbeats) has arrived for more than 30 min, and a footer line shows the last box update. `/healthz` returns `ok` on both. `/healthz/mirror` (VPS) returns JSON lag, and `/sync/status` (token) returns counts. On the box, status.json is rewritten every 5 min with box-side and VPS-side lag, and lag.log records state changes plus any pause longer than 45 s ("GAP"). Nothing is ever sent to Daniel or a bot. `python -m briefs.mirror verify` compares per-table digests of both DBs.

**Auth.** The VPS app keeps the Tailscale identity gate (only <USER_EMAIL>; proxied requests without identity get 403, checked). `/sync/*` is the only path that skips the identity gate and it requires `Authorization: Bearer <token>` (constant-time compare). The token is generated on the box, copied to /var/lib/briefs/secrets/sync-token (600), and never printed. Without it you get 401, also over the tailnet URL. It is reachable only through tailnet Serve, not Funnel.

**Backups (VPS).** Nightly 07:30 UTC (03:30 ET) via the SQLite backup API, integrity checked, mode 600, newest 7 kept: **/var/lib/briefs/backups/briefs-YYYYMMDD-HHMMSS.db**. The box's nightly Drive backup can include that directory later (needs a pull from the VPS; I did not touch the backup routine).

## Test results
Scratch databases were used throughout; no test data was ever put in the real box or VPS DB. I tested first on loopback with a scratch box DB and a scratch VPS app, then repeated a, b and c over the real tailnet path (SOCKS → Serve :8443 temporary → token) with scratch DBs on both ends. The scratch Serve mapping, instance and files were then removed.
- **(a) PASS.** Publish and republish (report_items replaced) on the box appeared on the VPS UI/API and in search. Digest comparison matched.
- **(b) PASS.** An action created on the VPS UI arrived in `brief inbox` on the box exactly once (replays and 3 simultaneous pulls gave 1 row; the box mapping table prevents duplicates). The ack and the item status showed on both. Queued → submitted, and box-wins after `processing`, are covered in the unit tests.
- **(c) PASS.** With the VPS stopped, publish and ack on the box queued up (daemon logged the outage, rate limited). After restart everything drained, digests matched, no duplicates.
- **(d) PASS (dry run only).** The real webhook was not fired. Dry run with the real settings built the right payload (host and key redacted). The retry and backoff path was tested against a local fake endpoint: fail, spool, retry fails, retry succeeds, spool cleared.
- **(e) PASS.** `curl --socks5-hostname localhost:1055 https://grok-bot-box…/healthz` → ok; `https://vps-primary…/healthz` → ok.
- Real backfill done: 13 bots / 197 reports / 1739 items / 4032 report_items / 0 actions on both; `verify` matches. The daemon also survived two real box freezes (67 s and 881 s) and tailscale blips, and monit restarted it once.
- Pytest: 59 passed, 1 failed. The failure `test_m2.py::test_snooze_hides_then_reappears` was already failing before my changes (hard-coded snooze date 2026-10-25 is now in the past). 7 new tests in tests/test_mirror.py.

## Changes (backups `*.bak-20261031`)
- Dashboard repo (box): new briefs/syncapi.py, briefs/mirror.py, ops/vps/*, tests/test_mirror.py, README section 10. Small edits: briefs/web.py (+32 lines; all inert unless BRIEFS_SYNC_TOKEN_FILE is set, which is only on the VPS), briefs/templates/base.html (banner and footer, only shown on the VPS), briefs/cli.py (inbox/actions call a silent best-effort pull). The running box uvicorn was not restarted; it will load the edits at its next normal restart with no behaviour change.
- Box watchdogs: wd-svc (+ `bin/wd-svc.bak-20261031`) got a `briefs-mirror` start/stop; new conf.d/briefs-mirror.monitrc. Monit reloaded.
- Box DB: one additive table `mirror_vps_actions`, created when the first VPS action is pulled. It does not exist yet. The box DB is otherwise unchanged.

## Roll back
- Stop mirroring only: `monit -c ~/.config/monit/monitrc stop briefs-mirror`, then delete watchdogs/conf.d/briefs-mirror.monitrc and `monit reload`. The `brief inbox` pre-pull then does nothing harmful (disable with `BRIEFS_MIRROR_HOOK=0`, or delete ~/.config/briefs-mirror/token).
- Instant code rollback on the box: `cd dashboard && for f in briefs/web.py briefs/cli.py briefs/templates/base.html; do cp -p $f.bak-20261031 $f; done` (and `cp -p watchdogs/bin/wd-svc.bak-20261031 watchdogs/bin/wd-svc`).
- Remove the VPS copy: `ssh <vps-ssh-alias>`; `export XDG_RUNTIME_DIR=/run/user/0; systemctl --user disable --now briefs-web.service briefs-wake-retry.timer briefs-backup.timer; tailscale serve --https=443 off; rm -rf /root/.config/systemd/user/briefs-* /var/lib/briefs` (optionally `loginctl disable-linger root`). VPS-made actions not yet pulled would be lost, so check `/sync/status` pending_for_box = 0 first.

## Needs Daniel
Nothing blocks dual-run. Serve needed no approval. Optional or for later:
1. Open https://vps-primary.<tailnet>.ts.net/ in your browser to confirm the login gate lets you in (I could only test from the box, which tailnet sees as your device).
2. First real Send/Submit all on the VPS fires the real Chief of Staff webhook. That path was verified only by dry run and a local fake endpoint.
3. To make the units declarative later, add them to the flake in site-djbclark (I did not push to git or deploy).

## Known risks
- The services are outside the NixOS config (user units under linger). A future flake change that resets /var/lib/systemd/linger or user@0 would stop them until re-enabled.
- They run as root (NixOS users are declarative; I did not add one). The app only listens on 127.0.0.1.
- The webhook URL/key now also exist on the VPS (mode 600, root).
- Real-bot publishes have not yet been observed arriving (no bot published during my session; the box mirror had nothing new to send after the backfill). The same code path was exercised by the scratch tests. Check `status.json` / `lag.log`, and `verify`, after the next bot run.
- Wake race: if the box is frozen, Chief of Staff can be woken before the actions reach the box DB; they are then picked up by the next `brief actions`/`inbox` read (pre-pull) or poll, and each bot sees them on its next run as before.
- Between a VPS action and the box pulling it (≤10 s normally), VPS and box item states can differ briefly.
- items_fts search text for mirrored updates is rebuilt from the latest report_item body, which matches how publish builds it.
- The Cleanup tab (file-based Gmail data) is not mirrored; on the VPS it shows "No cleanup dataset".
