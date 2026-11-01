# Coding bot: box coding / TUI setup (paths only, no secrets)

Collected 2026-10-14 from the box (Techno Euphora / One-offs work). Coding bot agent id <AGENT_ID>.

## 1. acp-run (drive any coding agent headless over ACP)
- Script: ~/.local/share/acp-run/acp-run (wrapper on PATH: ~/.local/bin/acp-run); doc: ~/.local/share/acp-run/README.md. Backups: acp-run.bak-pre-{webhook,zed,cline,cursor,claude-default,deepseek-default}, acp-run.orig.
- Usage: `acp-run --list`, `acp-run <agent> --info`, `acp-run <agent> -C <cwd> -p '...' [--model M] [--perm all|deny|scoped:P] [--timeout S]`, interactive `-i [--inbox DIR] [--resume ID]`.
- Logs: ~/.local/state/acp-run/ (JSONL per run). Exit codes in README (0 ok, 1 fail/delivery check, 124 timeout, 75 load guard).
- Box-specific tables at top of the script: AGENTS, DEFAULT_SET, YOLO_MODE/YOLO_SET/YOLO_CMD, CLIENT_NAME, GLOBAL_RULES, HANDSHAKE_TIMEOUT. Yolo is the default for all agents (Daniel 2026-10-03).
- Defaults: claude = sonnet, effort high; cline = provider cline-pass, model cline-pass/deepseek-v4.1-flash. Policy: agy by default, Claude only for complex coding/reviews.

## 2. agy via refined-antigravity-acp (zed clientInfo)
- `acp-run agy` launches ~/.local/bin/refined-antigravity-acp (override env ACP_RUN_AGY_CMD) -> node22 at ~/.local/opt/node22, package ~/.local/opt/raacp/.../@simonepri/refined-antigravity-acp. Official .par crashes on the box (no nobody group).
- CLIENT_NAME agy = "zed": agy_acp_server only offers non-Gemini models (Claude, GPT-OSS) to zed/jetbrains/xcode clients; as "acp-run" a Claude --model fails.
- Yolo via session/set_mode "yolo". Global rules ~/.gemini/GEMINI.md injected as _meta.systemPrompt (agy ACP loads none itself). GEMINI_HOME shadow home avoids the Orca hook in ~/.gemini/config/hooks.json (ensure_agy_home()).
- agy TUI: ~/.local/bin/agy; data ~/.gemini/antigravity-cli, ~/.gemini/antigravity-acp.

## 3. Cursor CLI over ACP
- `acp-run cursor --model composer-2.5 -C <cwd> -p ...` runs `cursor-agent acp` (~/.local/bin/cursor-agent, also ~/.local/bin/agent); auth method cursor_login. No auto-approve mode; CLIENT_NAME cursor = "acp-run" (set explicitly 2026-10-14). acp-run sends ClientCapabilities `_meta.parameterizedModelPicker: true` for cursor (2026-10-14), so cursor offers plain model ids plus separate `context`, `reasoning_effort`, `fast` options: e.g. `acp-run cursor --model grok-4.7 --set context=500k --set fast=false --set reasoning_effort=high`. Bracketed preset strings (`grok-4.7[context=256k,...]`) and CLI ids (`grok-4.7-high`) no longer apply to cursor; `--info` lists the options. Model is applied before the other --set keys.
- Config: ~/.cursor (rules/, skills/, skills-cursor/, acp-sessions/, sandbox-policies/).

## 4. Claude Code
- TUI ~/.local/bin/claude; ACP adapter ~/.local/bin/claude-agent-acp (@agentclientprotocol/claude-agent-acp; ~/.local/opt/claude-acp). Global rules ~/.claude/CLAUDE.md; skills ~/.claude/skills.
- acp-run: yolo = mode bypassPermissions, handshake 90 s, falls back to `claude -p` if ACP session never starts.

## 5. Cline
- ~/.local/bin/cline (sh wrapper pinning Node 22 v22.23.3 at ~/.local/opt/node22; cline 3.0.73, reinstalled 2026-10-23), `cline --acp --auto-approve true`. Rules ~/.cline/rules, skills ~/.cline/skills. `npm install -g --prefix ~/.local cline@...` recreates ~/.local/bin/cline as a symlink: restore the wrapper afterwards.
- Auth (2026-10-23): ClinePass **API key**, not OAuth. Provider cline-pass, model cline-pass/deepseek-v4.1-flash, in ~/.cline/data/settings/providers.json (600). Default account: <second-account>. To switch accounts, edit the key in providers.json (0600) rather than passing it with `-k`: `-k <key>` puts the key in process argv (visible in /proc to every local user) and also saves it. Keys are also in SecretSpec as CLINE_API_KEY (<second-account>) / CLINE_API_KEY_DC (see section 6).
- acp-run: standing operator approval `ACP_OPERATOR_APPROVED=cline` in ~/.config/acp-run.env, so `acp-run cline ...` works unattended. A harmless 'Event loop is closed' traceback can appear on stderr at exit.

## 6. Usage tracking: CodexBar / aiuse
- ~/.local/bin/codexbar, ~/.local/bin/aiuse; config ~/.config/aiuse/config.toml. Only collector codexbar is enabled, with providers = "cursor,clinepass" (clinepass added 2026-10-23). Native [collectors.clinepass] stays disabled.
- CodexBar ClinePass: ~/.config/codexbar/config.json (600), clinepass tokenAccounts labelled <second-account> (active) and dc. Both: `codexbar usage --provider clinepass --all-accounts`.
- SecretSpec 0.21.1 (~/.local/bin/secretspec). Global default provider is dotenv:///home/box/.config/aiuse/secrets.env (600), set in ~/.config/secretspec/config.toml. aiuse manifest ~/.config/aiuse/secretspec.toml declares CLINE_API_KEY and CLINE_API_KEY_DC. sudo-secretspec is NOT installed.
- Limitation (aiuse 3.9.4): `ai` shows only the <second-account> ClinePass row. The codexbar collector doesn't pass --all-accounts, and the native clinepass collector takes one unlabelled key that gets cross-checked against the wrong account. Upstream fix is pending in github.com/djbclark/aiuse (~/src/aiuse). Until then use the codexbar command above for dc.
- `aiuse` (pretty), `aiuse --json`, `aiuse --available`, `aiuse --providers cursor`.

## 7. grok-bot-messaging (agents -> bots)
- `notify-bot <bot> -f <agent> -m <model> -s <subject> [-r] [-n] "msg"`, `notify-bot --list` (~/.local/bin/notify-bot). `notify-techno-euphora` is a wrapper.
- Registry ~/.config/agent-webhook/bots.json; doc /home/box/agent-data/shared/agent-webhook/README.md; logs /home/box/agent-data/shared/agent-webhook/<bot>/received.log.
- Skill /home/box/agent-data/shared/agent-webhook/skill/grok-bot-messaging, symlinked into ~/.claude/skills, ~/.gemini/skills, ~/.cursor/skills, ~/.cline/skills, ~/.agents/skills. Global rule pointers: ~/.claude/CLAUDE.md, ~/.gemini/GEMINI.md, ~/.cline/rules/techno-euphora-webhook.md, ~/.cursor/rules/techno-euphora-webhook.mdc.
- Secret names (env, never print): AGENT_WEBHOOK_CODING_URL/_KEY[/_HEADER]; techno-euphora uses AGENT_WEBHOOK_TECHNO_EUPHORA_* or the legacy unsuffixed AGENT_WEBHOOK_URL/_KEY/_HEADER.
- Coding bot webhook routine draft: /home/box/agent-data/shared/agent-webhook/coding/routine-prompt.md.

## 8. Forgejo (forgejo.mit.edu) and Hyades CI (2026-10-14)
- Skill `forgejo-box` (/home/box/agent-data/shared/forgejo-box/skill/forgejo-box) plus `forgejo` (~/src/djbclark-ade/skills/forgejo) and `forgejo-ci` (~/src/loopback-port-shim/skills/forgejo-ci), symlinked into ~/.claude, ~/.gemini, ~/.cursor, ~/.cline, ~/.agents skills.
- Wrappers on PATH: forgejo-api, git-forgejo, tea-forgejo (tea 0.16.0 in ~/.local/bin), forgejo-mirror-github; secret name FORGEJO_TOKEN (env, never print). Shim: ~/.local/lib/portshim.so.

## acp-run source (2026-10-14)
- ~/.local/bin/acp-run is a symlink to ~/src/djbclark-ade/tools/acp-run/acp-run (same as Daniel's Mac). Update with `git -C ~/src/djbclark-ade pull`. The old installed copy and backups remain in ~/.local/share/acp-run (acp-run.bak-pre-repo-merge = last box-only version).
- Box behavior comes from auto-detect in the repo script: agy uses ~/.local/bin/refined-antigravity-acp (override ACP_RUN_AGY_CMD / ACP_RUN_AGY_SERVER), yolo via session mode; cline uses ~/.local/bin/cline (Node 22 wrapper; override ACP_RUN_CLINE_CMD); agy gets ~/.gemini/GEMINI.md as _meta.systemPrompt.
- Box default models: ~/.config/acp-run.env (ACP_RUN_DEFAULT_SET_CLAUDE="model=sonnet,effort=high", ACP_RUN_DEFAULT_SET_CLINE="provider=cline-pass,model=cline-pass/deepseek-v4.1-flash"). acp-run reads this file itself (KEY=VALUE lines, no shell expansion; variables already set in the environment win; ACP_RUN_ENV_FILE overrides the path), so every caller sees it, including agent tool calls, cron and plain `bash -c`.
- Logs: ~/.local/state/acp-run (unchanged).
