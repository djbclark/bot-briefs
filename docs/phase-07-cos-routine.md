# Phase 7: Chief of Staff daily high-priority routine

[Back to README](../README.md) | Previous: [phase-06-bot-routines.md](phase-06-bot-routines.md) | Next: [phase-08-connectors.md](phase-08-connectors.md)

## How the CoS runs this phase

1. Explain in 2-4 sentences what this phase does and why. Mark what is Grok-Bot-specific vs generic.
2. Show the relevant repo files/examples. Anything marked `EXAMPLE TEMPLATE, DO NOT USE AS-IS`: show as an example, ask the interview questions, draft the user's own version, get an explicit OK.
3. Ask before running anything that installs, overwrites, sends, spends, exposes a service, or is destructive.
4. Do the steps. Prefer `scripts/install.sh --phase 7` (dry run) then `--apply` for file copies.
5. Run the Verify section and report results honestly.
6. Record progress (PROGRESS.md + memory) and wait for the user before the next phase.

## Guide (from the operator's working system)

```text
Show examples/routines/cos-daily-high-priority.md (the operator's prompt, with generic examples), interview
(questions at its end), draft, create on the CoS with UpdateRoutine (e.g. 6:16 AM daily).
Pipeline: brief inbox --bot cos -> cos-gather -o /workspace/cos-gather.json --explain -> CoS ranks the whole pool -> ReadTranscript
for bots missing from the dashboard -> /workspace/report-YYYYMMDD.md (`**Most urgent:**`, `**1. Title**`, `1.1 text`; the chat post uses `***1.***` / `> ***a.***`, see docs/chat-format.md) -> report-to-items
-> brief publish --bot cos -> ref-append CHIEF -> chat post with link. Updates shared/source-health.json.
Auto-review fallback (when the classifier errors, not when it blocks):
  acp-run agy -C /workspace -p 'Run this exact shell command and show its output, change nothing else: <cmd>' --model <fast model> --set thought_level=low
Verify: "run your daily high-priority routine now"; check chat post, `cos` report, Ref line.
```

## Notes

Example: [examples/routines/cos-daily-high-priority.md](../examples/routines/cos-daily-high-priority.md). The pipeline script is [bin/cos-gather](../bin/cos-gather) (personalize its slugs and TODO sources).

## Verify

- Ask the CoS: "run your daily high-priority routine now"; check the chat post, the `cos` report on the dashboard, and the Ref line.
- `python3 -m json.tool /workspace/cos-gather.json | head` shows the gathered pool.

Done when the checks pass and the user agrees. Then update PROGRESS.md: `phase 7 done <date>; next 8`.
