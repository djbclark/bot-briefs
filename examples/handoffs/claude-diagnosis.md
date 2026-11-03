<!-- Handoff: diagnosis prompt (dashboard down) given to Claude Code. EXAMPLE TEMPLATE, DO NOT USE AS-IS: contains operator-specific hosts/paths; adapt. -->

Task: the Bot Briefs dashboard at https://grok-bot-box.<tailnet>.ts.net/ is unreachable (reported by <OPERATOR_NAME>, Sat Oct 31 2026 10:50 AM ET). Diagnose the root cause and fix it. You own this fix; Chief of Staff deliberately did not touch anything.

What Chief of Staff observed (read-only):
- Dashboard app itself is fine: `curl http://127.0.0.1:8642/api/reports` returns 200.
- From the box, `curl https://grok-bot-box.<tailnet>.ts.net/` fails (http code 000, ~6s).
- tailscaled runs as pid 3802198 in userspace-networking mode: `/home/box/.local/bin/tailscaled --tun=userspace-networking --socks5-server=localhost:1055 --outbound-http-proxy-listen=localhost:1056 --statedir=/home/box/.local/share/tailscale --socket=/home/box/.local/share/tailscale/tailscaled.sock`. The default socket path /var/run/tailscale does not exist, so always pass `--socket=/home/box/.local/share/tailscale/tailscaled.sock`.
- `tailscale serve status` (with that socket) still shows https://grok-bot-box.<tailnet>.ts.net proxying / to http://127.0.0.1:8642 and tcp 2222, so serve config is intact.
- `tailscale status --json` returned non-JSON; it printed a warning: client version "1.104.1-t7f4efe814-g8da26756c" != tailscaled server version "1.104.1-t7f4efe814-timejump". The "timejump" daemon build is suspicious (a time-jump patched build?). Investigate it.
- Box uptime 4 days 6:48, load normal.

Known history (memory): on 2026-10-24 the same symptom happened because the box tailscaled lost its control-plane connection (Self.Online=false, no netmap) while local checks passed; restarting tailscaled fixed it, and a watchdog `wd-ts-health` was extended to fail on Self.Online=false and restart after 4 min. Check why that watchdog did not recover this time (find it under /home/box/bin or crontab/systemd user units; read its logs). Also check: DNS resolution of the ts.net name from the box, tailscale netcheck, node key expiry, whether the <TAILNET-IP> node is online in `tailscale status`, and logs of tailscaled (find where it logs).

Rules: restarting tailscaled and the dashboard on the box is allowed and expected if needed; do not touch the operator's herdr sessions; do not delete data or state; do not change anything on the operator's Mac; do not log out of tailscale or remove the node (re-auth would need the operator). If a fix needs the operator (for example re-authentication or a key approval), stop and say exactly what he must do. Verify the fix by fetching the https URL (expect the login page or a 200/302/401 from the dashboard, not 000) and, if possible, state whether it is reachable from the tailnet side.

Report: root cause, what you changed, how you verified, and whether the watchdog needs a fix (and make that fix if it is small and safe). Keep the report short and in plain words.
