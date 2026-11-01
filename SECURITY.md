# Security model

This repo is an architecture example. Read this before copying any of it.

## Threat model
- **Bots read attacker-controllable content.** Email, mailing lists, chats, GitHub issues, web pages and upstream
  commits can all contain instructions aimed at an AI agent (prompt injection). Assume any bot that reads them
  may be steered by a stranger.
- The goal is to keep a steered bot from turning into a full compromise: limited secrets, limited write
  access, nothing that sends or pays without a human.

## Defaults this repo expects
1. **Least privilege (you must set it).** The bundled `acp-run` defaults to `--perm all` (yolo) unless
   `ACP_RUN_DEFAULT_PERM` says otherwise. Recommended: put `ACP_RUN_DEFAULT_PERM=read-only` in
   `~/.config/acp-run.env` and pass `--perm all` only for a task a human approved. `--perm read-only` is
   enforced for claude, codex, opencode and zcode; for other agents `acp-run` refuses it with an error rather
   than running writeable. Never fall back to `--perm all` when read-only is refused.
   `acp-run`'s environment filter is a **denylist**: it drops credential-looking variables but keeps vendor API
   keys (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, ...) and re-adds `ACP_RUN_PASS_ENV` under `--perm all`.
   Agents also run as the same Unix user, so they can read your 0600 files in `~/.config` and `~/.ssh`
   whatever the env holds. Run agents as a separate user if that matters to you.
2. **No yolo for bot-triggered agents.** Auto-approve modes are for a human at the keyboard only; see item 1
   for how to make that the default.
   Never route around a safety reviewer: if it errors, retry once, then report to the user.
3. **Per-bot / per-client tokens.** Every webhook, MCP gate and service gets its own token so one leak can be
   revoked alone. Rotate tokens on a schedule. Prefer tailnet-only `tailscale serve` over public Funnel.
   Pass tokens on stdin or in 0600 files (`curl -K -`, `-H @file`), never in argv.
4. **Validate payload fields.** Webhook fields such as `from` and `cwd` are untrusted: allow-list them,
   keep paths under allowed roots, and quote with argv or `printf %q`.
5. **Don't run other people's code with your secrets.** `repo-sync` only runs tests for repos on your
   allowlist, with a minimal env (PATH, HOME, LANG, TERM).
6. **Encrypt backups** and give the backup job its own passphrase file (see `shared-docs/restic-backup.md`).
7. **Draft, never send.** Bots draft messages; a human sends.

## Personal data
- **Never put personal data in examples**: family or VIP names, addresses, account numbers, health details,
  private repo names, folder IDs, agent IDs, tailnet names. Use placeholders such as `<FAMILY_MEMBER_1>`,
  `<VIP_1>`, `<USER_EMAIL>`, `<FOLDER_ID_...>`, `<AGENT_ID>`, `<tailnet>`.
- Keep real people lists (VIPs, family) in private config outside the repo.
- Before committing, run `scripts/secret-scan.sh` (common secret patterns, plus your own deny-list of personal
  terms kept in a file **outside** the repo; never commit that list).

## Known gaps in the example scripts
- `acp-run` defaults to yolo and filters env by denylist (see Defaults, item 1). Set
  `ACP_RUN_DEFAULT_PERM=read-only` before letting bots trigger it.
- The dashboard trusts the `Tailscale-User-Login` header. Tailscale Serve connects from 127.0.0.1, so any local
  process that knows the allowed login can forge it and create actions recorded as the owner. Treat code
  running on the box as able to act as the owner in the dashboard.
- Git history of the original repo may contain unscrubbed text; share a squashed copy.
