# Box watchdogs (monit)

Set up 2026-10-24 after tailscaled lost its peers following a gateway/self-IP change while its process stayed up.
The box is a container with no systemd and no cron. **Monit 5.34** (apt package `monit`) runs as user `box`.
It checks each service every 30 s for liveness and, where possible, real health.

Why monit: it is maintained, packaged, runs unprivileged, and does health checks (HTTP, TCP, or a custom program),
not just "is the PID alive". supervisord, runit, and s6 only restart processes that exit. They would not have caught the
8:30 AM wedge. One conf.d of drop-ins is the same idea as the Mac's watchdogd (`~/.config/djbclark/watchdogd.d/*.toml`).
Backoff follows load-watch's job guard idea: cap restarts instead of looping on crashes.

## Layout
| Path | What |
|---|---|
| `~/.config/monit/monitrc` | main config (mode 0600). Includes `conf.d/*.monitrc` |
| `/home/box/agent-data/shared/watchdogs/conf.d/services.monitrc` | the service checks |
| `.../watchdogs/bin/wd-svc` | `start\|stop\|restart NAME`: start/stop commands, plus restart backoff |
| `.../watchdogs/bin/wd-ts-health` | functional tailscaled check |
| `.../watchdogs/bin/wd-dashboard-url` | end-to-end check of the dashboard's tailnet URL (`/healthz` through the SOCKS proxy must return `ok`), every 60 s; see 2026-10-28 note |
| `.../watchdogs/bin/wd-dashboard-tasks` | dashboard chores: re-apply the Serve mapping (tailnet-only), nightly import after 03:45 ET, trim logs |
| `.../watchdogs/bin/watchdog-guardian` | 60 s loop that restarts monit if it dies (monit watches the guardian too) |
| `.../watchdogs/bin/watchdog-ensure` | idempotent startup hook |

## Logs (`~/.local/state/watchdogs/`)
- `watchdog.log`: every START/STOP, backoff deferral, tailscaled health failure and recovery, Funnel warning, and guardian restart of monit (box time, ET)
- `monit.log`: monit's own log (UTC timestamps)
- `NAME.starts`: start timestamps used for backoff
- Service logs: `~/.local/share/tailscale/tailscaled.log`, `~/.local/share/mac-mcp-forwarder/forwarder.log`,
  `dashboard/logs/server.log`, `~/.local/share/briefs-dashboard/keepalive.log`, `~/.beeper/logs/keepalive.log`

## Status and control
```
M="monit -c ~/.config/monit/monitrc"
$M summary            # or: $M status tailscale-health
$M restart dashboard  # $M stop/start/unmonitor/monitor NAME
$M reload             # after editing conf.d
```

## What's watched
| Service | Liveness | Health → action |
|---|---|---|
| tailscaled (userspace; SOCKS :1055, HTTP proxy :1056) | process match | TCP :1055 fails 3 cycles → restart. **tailscale-health** (every 60 s): the local API answers, a `tailscale ping -c 2 --timeout 5s` reaches an always-on peer (<TAILNET-IP>) or vps-primary <TAILNET-IP> (never the Mac alone), and `curl --socks5-hostname localhost:1055 https://grok-bot-box.<tailnet>.ts.net/` returns 200. Restarts only after **4 min of continuous failure**, which covers the 05:00–05:05 ET home-network reboot. Also logs a WARNING if Funnel is ever on |
| mac-mcp-forwarder (127.0.0.1:18800) | process | `/__fwd_health` fails 2 cycles → restart |
| dashboard (uvicorn 127.0.0.1:8642) | process | `/healthz` fails 2 cycles → restart (a wedged/SIGSTOPped process is SIGKILLed) |
| dashboard-tasks | every 5 min | Re-applies `serve --bg --https=443` → 127.0.0.1:8642 if the mapping is missing. **Tailnet-only; never Funnel** |
| beeper-server (127.0.0.1:23374) | process | `/v1/info` fails 3 cycles → `beeper targets start server -y` |
| risk-watch loop | process | `risk-watch start` |
| guest-sshd (127.0.0.1:2222, Serve tcp 2222) | process | TCP fails 2 cycles → restart (`sudo -n sshd -f /etc/ssh/sshd_guest.conf`) |
| cline hub (127.0.0.1:25463) | monitor only | started on demand by the cline CLI, so it is not restarted |
| watchdog-guardian | process | restarted by monit |
| hermes-quota (`conf.d/hermes.monitrc`, `bin/wd-hermes`) | every 30 cycles (15 min) | Mac port 22 via SOCKS :1055 → `change-hermes --remote --check` (one tiny real request to Hermes' `fallback_providers[0]` route). rc 10 → `change-hermes --remote --auto`, backoff 30 min·2^(n-1), cap 4 h (`hermes-auto.runs`, 6 h window). Mac off/asleep = skip (exit 0); 04:58–05:10 ET skipped. rc 73 (busy: the Mac-side lock is held) = skip, not counted as a run. Exit 1 → monit alert only for ssh auth refused or `--auto` finding nothing |

**Backoff** (in `wd-svc start`): with n starts in the last 30 min, the next start must wait 30 s·2^(n-1), up to 15 min.
A deferred start is logged and monit retries on a later cycle.

## Startup
There is no init system. `~/.bashrc` and `~/.profile` already call `~/.local/bin/mac-mcp-ensure` and `~/.local/bin/dashboard-ensure`,
and the agent shells source them at container start and on every shell. Both are now shims that run `watchdog-ensure`.
That script starts monit and the guardian if either is missing. Backups of the old scripts are `*.bak-pre-monit-20261024`.
The old loops (`mac-mcp-keepalive.sh`, `dashboard/ops/dashboard-keepalive.sh`, `beeper-server-keepalive.sh`) are retired: each now exits at line 2.

## Adding a process
1. Add a `start)` line and a `stop)` line for NAME in `wd-svc`. Detach with `bg ...`, which runs setsid and nohup. Add a `pkill -9` line if the process can wedge.
2. Append to `conf.d/services.monitrc` (or add a new `conf.d/NAME.monitrc`):
```
check process NAME matching "unique cmdline regex"
    start program = "/home/box/agent-data/shared/watchdogs/bin/wd-svc start NAME"
    stop program  = "/home/box/agent-data/shared/watchdogs/bin/wd-svc stop NAME"
    if failed host 127.0.0.1 port PORT protocol http request "/health" for 2 cycles then restart
```
   For a custom check, use `check program X with path "/script"` and `if status != 0 then exec "... wd-svc restart NAME"`.
   Build the tolerance into the script (see `wd-ts-health`).
3. Run `monit -c ~/.config/monit/monitrc -t && monit -c ~/.config/monit/monitrc reload`. Then kill the process and check `watchdog.log`.

## Hermes quota watchdog (added 2026-10-24)
After Hermes ran out of model quota at about 03:18 ET (cline2 DeepSeek Flash daily cap, then the xAI grok credits).
- `~/.local/bin/change-hermes` → `/workspace/djbclark-ade/bin/change-hermes` (repo djbclark/djbclark-ade, master). Off macOS it runs itself on the Mac
  over `ssh <mac-hermes-ssh-alias>` (`~/.ssh/config`, key `~/.ssh/<your_hermes_key_name>`, via tailscale SOCKS). On the Mac the key's authorized_keys line forces
  `bin/change-hermes-ssh-gate`, which allows only `--check`, `--list`, `--auto [--dry-run]`.
- `--auto` order: cline2 DeepSeek Flash → cline Flash → cline2 Pro → cline Pro → cline2 kimi-k3 → glm-5.2 → minimax-m3 → qwen3.7-max → xai-oauth grok-4.7 (aiuse headroom ≥ 15%).
  It applies the first route that answers (effort medium, native context) to every side-model slot. It does not touch `model.*`. Running Hermes sessions keep the old config until they restart.
- Concurrency (2026-10-24): every run executes on the Mac, so change-hermes locks there: `~/.hermes/state/change-hermes.lock` (mkdir lock, owner.json with pid/host/time; stale when the pid is dead or older than 20 min). `--auto` and config writes hold it; a run that waits 30 s without getting it exits 73 (busy), which wd-hermes and the Mac's watchdogd treat as a skip. Config is written to a temp file and renamed.
- Shared cooldown: a successful `--auto` (from the box or the Mac) stamps `~/.hermes/state/change-hermes-auto.json` on the Mac; another `--auto` within 30 min only re-checks (rc 0 / 10) instead of switching.
- The Mac runs the same watch itself: watchdogd entry `hermes-quota` (`~/.config/djbclark/watchdogd.d/hermes-quota.toml`, rendered from site-djbclark `roles/watchdogd`), every 15 min, `--auto` on rc 10. The lock and cooldown keep the two from stepping on each other.
- `hermes-ping` is not used for the check: it sends a Telegram message.
- Test by hand: `/home/box/agent-data/shared/watchdogs/bin/wd-hermes; echo $?; tail ~/.local/state/watchdogs/watchdog.log`

## 2026-10-24 18:3x ET — wd-ts-health fast restart for stale control long-poll
Cause: after box pauses (>~1 min "time jump detected"), tailscaled's control connection is dead but reused; it logs
"map response long-poll timed out" every ~2 min and never recovers until restarted. Change: when Self.Online=false AND the
last 400 log lines contain "long-poll timed out", restart after `WD_TS_FAST_SUSTAIN` (default 75 s) instead of 240 s.
Backup: `bin/wd-ts-health.bak-20261024`. Rollback: `cp -p bin/wd-ts-health.bak-20261024 bin/wd-ts-health`.

## 2026-10-24 18:45 ET — wd-ts-health `ss -K` stopgap before restart (tailscale#12021, PR#21834)
Fast path now needs Self.Online=false AND >=2 "long-poll timed out" lines in the last 400 log lines *after the last
"Program starting"*. On the first such check of a failure episode it runs `sudo -n ss -K` on tailscaled's (pid, uid box)
ESTABLISHED TCP conns to dport 443 at the control IPs (198.18.0.1 = egress fake-IP that controlplane/login.tailscale.com
resolve to here, plus whatever getent returns), logs each kill (sport, dst, rc), waits `WD_TS_SSK_WAIT` (20 s), re-checks
Self.Online: if online -> logs "recovered after ss -K (no restart)", exit 0; else the existing 75 s restart still applies.
Once per episode (marker `~/.local/state/watchdogs/ts-health.ssk`). Unprivileged ss -K gets EPERM (CapEff=0); needs sudo -n.
Env: `WD_TS_SSK_DRYRUN=1` (log only), `WD_TS_SSK=0` (disable). Note: 198.18.0.1:443 may also carry logtail; acceptable.
Backup: `bin/wd-ts-health.bak-20261024b`. Rollback: `cp -p bin/wd-ts-health.bak-20261024b bin/wd-ts-health`
(or `.bak-20261024` to drop the fast path too).

## 2026-10-24 19:00 ET: patched tailscaled test (TS_DROP_NOISE_ON_TIME_JUMP)
- /home/box/.local/bin/tailscaled is now a custom build of v1.104.1 plus upstream PR #21834 (drop Noise client when the map watchdog fires) plus a test patch that also drops the Noise client on any time jump when env TS_DROP_NOISE_ON_TIME_JUMP=1. Patch: /workspace/tailscale-test/time-jump-drop.patch. Binary build: /workspace/tailscale-test/tailscaled-patched.
- wd-svc line 15 (`start tailscaled`) sets TS_DROP_NOISE_ON_TIME_JUMP=1, so watchdog restarts keep it. wd-ts-health's 75 s fast restart and ss -K step are unchanged and still act as the safety net.
- Backups: /home/box/.local/bin/tailscaled.bak-20261024-prepatch and wd-svc.bak-20261024-pre-timejump (in the watchdogs bin folder).
- Rollback:
  cp -p /home/box/.local/bin/tailscaled.bak-20261024-prepatch /home/box/.local/bin/tailscaled
  cp -p /home/box/agent-data/shared/watchdogs/bin/wd-svc.bak-20261024-pre-timejump /home/box/agent-data/shared/watchdogs/bin/wd-svc
  /home/box/agent-data/shared/watchdogs/bin/wd-svc restart tailscaled
- Status: untested against a real box pause as of 19:44 ET. Look for "dropping noise client after time jump" in ~/.local/share/tailscale/tailscaled.log after a "time jump detected" line.

## 2026-10-28 12:4x ET: wd-dashboard-url (dashboard URL end-to-end watchdog)
Request: make sure https://grok-bot-box.<tailnet>.ts.net/ stays up. Before this, the local server was watched (monit `dashboard`, `/healthz`),
the Serve mapping was re-applied only when it was *missing* (every 5 min), and wd-ts-health restarted tailscaled after 4 min of failure. A mapping that existed but did not
serve, or a URL outage with no one told, was not covered. New monit check `dashboard-url` (every 2 cycles, ~60 s, depends on dashboard) runs `bin/wd-dashboard-url`:
1. If the local `/healthz` is not `ok`: do nothing (monit's dashboard check restarts uvicorn).
2. Fetch `https://grok-bot-box.<tailnet>.ts.net/healthz` via `--socks5-hostname localhost:1055`; success means the body is exactly `ok`.
3. On failure it records the start in `~/.local/state/watchdogs/dashboard-url.fail`. After 60 s (`WD_URL_REAPPLY_AFTER`) it re-runs `tailscale serve --bg --https=443 http://127.0.0.1:8642` once per episode (tailnet-only, never Funnel).
4. After 20 min (`WD_URL_NOTIFY_AFTER=1200`) of continuous failure it sends one `hermes-ping`, and one more when the URL recovers. `WD_URL_NOTIFY=0` disables pings.
5. Skips actions 04:58-05:10 ET. It never restarts tailscaled; wd-ts-health still does that (4 min, or 75 s with the long-poll signature).
Log lines `[dashboard-url]` go to `watchdog.log`. Rollback: delete the `dashboard-url` block from `conf.d/services.monitrc` (backup `services.monitrc.bak-20261028`) and `monit -c ~/.config/monit/monitrc reload`.

## wd-saner (added 2026-10-28, Coding)

Keeps `opencli saner ...` working on the box: starts `box-chrome` (display :13) if Chrome is down, restarts the opencli daemon once if the Browser Bridge extension is not connected, and checks the Saner login every 30 min. Pings via `hermes-ping` immediately if Saner needs a sign-in, or after 20 min of any other problem, and once on recovery. Runs from monit every 10 cycles (~5 min). Env: `WD_SANER_NOTIFY=0`, `WD_SANER_NOTIFY_AFTER`, `WD_SANER_LOGIN_EVERY`, `WD_SANER_DISPLAY`. State: `~/.local/state/watchdogs/saner.{fail,acted,pinged,login-checked}`. The extension lives unpacked in `/workspace/opencli-ext/ext` (loaded in the Chrome profile); keep that folder.
