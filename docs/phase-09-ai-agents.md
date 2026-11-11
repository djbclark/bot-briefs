# Phase 9: Other AI agents via acp-run

[Back to README](../README.md) | Previous: [phase-08-connectors.md](phase-08-connectors.md) | Next: [phase-10-networking.md](phase-10-networking.md)

## How the CoS runs this phase

1. Explain in 2-4 sentences what this phase does and why. Mark what is Grok-Bot-specific vs generic.
2. Show the relevant repo files/examples. Anything marked `EXAMPLE TEMPLATE, DO NOT USE AS-IS`: show as an example, ask the interview questions, draft the user's own version, get an explicit OK.
3. Ask before running anything that installs, overwrites, sends, spends, exposes a service, or is destructive.
4. Do the steps. Prefer `scripts/install.sh --phase 9` (dry run) then `--apply` for file copies.
5. Run the Verify section and report results honestly.
6. Record progress (PROGRESS.md + memory) and wait for the user before the next phase.

## Guide (from the operator's working system)

```text
acp-run (the files listed under 'Repo files' below (`scripts/install.sh --phase N`)) is a single-file `uv run --script` program (dep: agent-client-protocol) that runs one prompt
through any ACP agent headless:
  acp-run --list ; acp-run <agent> --info
  acp-run agy -C /workspace -p 'task' --model <model> --set thought_level=low
  acp-run claude -C ~/src/repo --effort high -f prompt.md
  acp-run cline|cursor|hermes -C dir -p '...'
Exit 0 ok, 1 fail, 124 timeout, 75 load guard. Logs ~/.local/state/acp-run/*.jsonl.
Install what the user has; each needs the USER's own login:
  * Claude Code: `curl -fsSL https://claude.ai/install.sh | bash`, run `claude` to log in; ACP adapter
    `npm i -g --prefix ~/.local @agentclientprotocol/claude-agent-acp`.
  * Antigravity CLI (agy) + refined-antigravity-acp (sends clientInfo "zed" so non-Gemini models are offered); Node 22.
  * Cline: `npm i -g --prefix ~/.local cline`, `cline auth`; set ACP_OPERATOR_APPROVED=cline in ~/.config/acp-run.env.
  * Cursor CLI: `curl https://cursor.com/install -fsS | bash`, `cursor-agent login`.  * Hermes Agent + hermes-acp.
Edit the AGENTS / DEFAULT_SET / YOLO tables at the top of acp-run.
Policy (example, adapt): agy by default; Claude Code --effort high for complex builds/reviews; self-contained prompt files
(examples/handoffs/claude-vps-build.md, examples/handoffs/claude-diagnosis.md); agents report back with
`notify-bot coding -f claude -s "done" "..."`.  Verify: `scripts/verify.sh 9`.
```

## Notes

`acp-run` is at [local-bin/acp-run](../local-bin/acp-run) with its [README](../local-bin/acp-run.README.md). Handoff examples: [claude-vps-build](../examples/handoffs/claude-vps-build.md), [claude-diagnosis](../examples/handoffs/claude-diagnosis.md). Config: [config/acp-run.env.example](../config/acp-run.env.example).

## Repo files and where they go (3 files)

Copy with `scripts/install.sh --phase 9` (dry run; add `--apply` to write; existing files are kept unless `--force`, which makes a `.bak-<timestamp>` first). Manual equivalent: `install -D -m <mode> <repo_path> <destination>`.

| repo path | destination | mode |
|---|---|---|
| [`local-bin/acp-run`](../local-bin/acp-run) | `/home/box/.local/bin/acp-run` | 755 |
| [`local-bin/acp-run.README.md`](../local-bin/acp-run.README.md) | `/home/box/.local/share/acp-run/README.md` | 644 |
| [`local-bin/refined-antigravity-acp`](../local-bin/refined-antigravity-acp) | `/home/box/.local/bin/refined-antigravity-acp` | 755 |

## Verify

```bash
scripts/verify.sh 9
```

Done when the checks pass and the user agrees. Then update PROGRESS.md: `phase 9 done <date>; next 10`.
