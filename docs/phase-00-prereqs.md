# Phase 0: Prerequisites and interview

[Back to README](../README.md) | Previous: none | Next: [phase-01-box-layout.md](phase-01-box-layout.md)

## How the CoS runs this phase

1. Explain in 2-4 sentences what this phase does and why. Mark what is Grok-Bot-specific vs generic.
2. Show the relevant repo files/examples. Anything marked `EXAMPLE FROM DANIEL, DO NOT USE AS-IS`: show as an example, ask the interview questions, draft the user's own version, get an explicit OK.
3. Ask before running anything that installs, overwrites, sends, spends, exposes a service, or is destructive.
4. Do the steps. Prefer `scripts/install.sh --phase 0` (dry run) then `--apply` for file copies.
5. Run the Verify section and report results honestly.
6. Record progress (PROGRESS.md + memory) and wait for the user before the next phase.

## Guide (from Daniel's working system)

```text
 * Grok Bot account with: multiple bots (CreateAgent), routines (UpdateRoutine), connectors (MCP), the shared Linux box
   (Shell), secrets/vault, ReadTranscript, SendToAgent. On another platform, map each to its equivalent.
 * Accounts to connect (phase 8). Optional: Tailscale account (free plan OK), a VPS, a Mac/PC, AI agent subscriptions.
Steps: 1) [interview.md](interview.md), save answers. 2) Check `whoami` = box, $HOME = /home/box, /home/box/agent-data/shared exists;
otherwise choose substitute paths now. 3) Clone or read this repo on the box (e.g. /home/box/src/bot-briefs-system).  Verify: `scripts/verify.sh 0`.
```

## Notes

Interview script: [interview.md](interview.md). Profile template: [config/personal-profile.template.md](../config/personal-profile.template.md).

## Verify

```bash
scripts/verify.sh 0
```

Done when the checks pass and the user agrees. Then update PROGRESS.md: `phase 0 done <date>; next 1`.
