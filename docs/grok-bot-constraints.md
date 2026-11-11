# Grok Bot product constraints

What is product-specific vs generic.

````text
GROK BOT PRODUCT CONSTRAINTS
Grok-Bot-specific (use the product tool):
 * Bots: CreateAgent / app UI. Own chat, memory, routines, connectors; ONE shared box filesystem.
 * Routines: UpdateRoutine (cron or webhook), made by the owning bot in its main chat. Subagents/executors cannot change routines
   or memory.
 * SendToAgent (bot-to-bot); ReadTranscript (CoS reads other bots' transcripts for briefs missing from the dashboard).
 * Connectors/plugins = MCP (OAuth/token); multi-account = multiple namespaces.
 * Outbound messages need approval cards (DraftExternalMessage). Auto-review checks shell/MCP calls; may block or error.
 * Secrets: box env secrets via secret request/vault. Computer connector: user's Mac/PC (ListMachines/machineId).
 * Skills: agent-data/workflows/<name>/SKILL.md (examples: examples/skills/).
Box facts: Linux container, user box, persistent, shared by all bots; NO systemd, NO cron (monit + routines); no /dev/net/tun
(Tailscale userspace); inbound only via tailnet; sudo may be limited; box can pause (time jumps).
Generic: scripts, dashboard, brief, monit, restic/rclone, Tailscale Serve, acp-run, opencli plugins, VPS mirror. Elsewhere,
replace routines with cron/systemd timers and webhooks with any HTTP endpoint that wakes your assistant.
````
