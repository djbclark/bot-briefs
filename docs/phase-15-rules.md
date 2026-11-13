# Phase 15: Rules and conventions

[Back to README](../README.md) | Previous: [phase-14-webhook-dispatch.md](phase-14-webhook-dispatch.md) | Next: [phase-16-troubleshooting.md](phase-16-troubleshooting.md)

## How the CoS runs this phase

1. Explain in 2-4 sentences what this phase does and why. Mark what is Grok-Bot-specific vs generic.
2. Show the relevant repo files/examples. Anything marked `EXAMPLE TEMPLATE, DO NOT USE AS-IS`: show as an example, ask the interview questions, draft the user's own version, get an explicit OK.
3. Ask before running anything that installs, overwrites, sends, spends, exposes a service, or is destructive.
4. Do the steps. Prefer `scripts/install.sh --phase 15` (dry run) then `--apply` for file copies.
5. Run the Verify section and report results honestly.
6. Record progress (PROGRESS.md + memory) and wait for the user before the next phase.

## Guide (from the operator's working system)

```text
 1. Every post ends with ONE `Ref: <BOT>-<base36>` line (e.g. `Ref: CHIEF-5t`) from bin/ref or ref-append; one global counter.
    Never type a Ref by hand; call ref once per post, paste verbatim.
 2. Dashboard first: `brief inbox` at start, act, `brief ack`. If brief fails, continue and mention it in one line.
 3. No reply sheets (retired 2026-10-27).
 4. Never send to anyone without the user's explicit approval card. Bots draft and remind. CoS never drafts to other people.
 5. Timing: bots 3:14 AM, CoS 6:16 AM, weekday-only stays weekday-only, Friday weeklies, skip 04:58-05:10 network reboot window.
 6. Silent only if nothing new AND nothing open; repeat unacted open items daily.
 7. Tell the user on disconnect: a source failing on BOTH routes twice in a row over >= 2 h -> say so, naming source + first
    failure; a single failure with the Mac asleep = one line. Track in shared/source-health.json.
 8. Privacy: private sources stay in the user's own chat; links as given; never invent details.
 9. Stable item keys; parseable report format; end with "N lower-priority items not shown" then the Ref line.
10. Auto-review: if the classifier ERRORS, run the exact command via agy; never reshape commands to evade a real block.
11. Shared script changes: keep .bak-<date> copies and note them in shared/*.md.
12. Tailscale Serve only; never Funnel for anything on the box.
13. AI work: do it with the box's own assistant bot; other vendors' agents/APIs only when the CoS says usage is over pace
    (see token-saving-rules.md rule 3).
14. Stalled UI steps: if any single web UI / browser-automation step takes more than 3 minutes, stop, tell the user
    what is stuck, and offer them control of the box desktop; afterwards look at what they did and learn from it.
15. Sign-ins: when a sign-in page offers "Sign in with Google" (or the box's other existing SSO session), click it
    first; hand over to the user only if the provider then asks for a password, 2FA or other input.
16. Mail thread links: link threads with the user's chosen mail client's official https thread URL (built from the
    connector's thread id); no webmail fallback links.
17. Chat posts: bold-italic item numbers, quote-block sub-items, Roman-numeral sections when there is more than one
    (chat-format.md), Ref line last.
```

## Token-saving and chat-format rules

Also offer these: [token-saving-rules.md](token-saving-rules.md) (scans only a few hours before the brief, zero-token pollers, AI work with the box's own assistant and other vendors only as an over-pace fallback, GUI work to the computer-use agent) and [chat-format.md](chat-format.md) (`***1.***` items, `> ***a.***` sub-items, `**I.**` / `**II.**` sections; dashboard files keep `**1. Title**` / `1.1`).

## Verify

- Read the rules back to the user one by one; save the confirmed list to memory and to `/home/box/agent-data/shared/bot-dashboard.md` (or a rules doc every bot reads).

Done when the checks pass and the user agrees. Then update PROGRESS.md: `phase 15 done <date>; next 16`.
