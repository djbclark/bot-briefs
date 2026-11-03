# Token-saving rules

Rules for keeping a multi-bot system cheap without losing what the user actually reads. Put the short version in
each bot's instructions and in the chief-of-staff's rules ([phase-15-rules.md](phase-15-rules.md)).

## 1. Scans exist to feed reports
Scanning and polling routines (inbox sweeps, list watches, "anything new?" checks) exist only to feed the daily
or weekly brief. Run them **once, a few hours before the brief** they feed, not every 15 minutes all day.
If something truly needs a faster reaction, it should be a zero-token poller (rule 2), not a frequent LLM routine.

## 2. Zero-token polling may run as often as you like
A script that checks deterministically and wakes a bot only on change (monit + `notify-bot` style watchdog)
costs no model tokens until it fires, so its frequency is not a budget question. See
[shared-docs/zero-token-pollers.md](../shared-docs/zero-token-pollers.md).

## 3. Coding work goes to installed coding agents over ACP
Send coding work to the coding TUIs installed on the box through `acp-run` (see
[local-bin/acp-run.README.md](../local-bin/acp-run.README.md)), in a preference order you write down
(for example: your main subscription agent first, then a second vendor, then a cheaper or local option).
For each task, choose deliberately:
- **vendor and model** (match the task; do not default to the most expensive),
- **effort / reasoning level**,
- **context window** (large repos need it; small fixes do not).

After the run, **verify from the agent's logs which model actually ran**. Agents can silently fall back to a
different model when a quota is hit or a flag is ignored. Record the vendor, model, and effort in the report.

Some vendors serve overlapping models (the same model family through more than one subscription or API). Count
that when choosing: the cheapest route to a model may be a different vendor than the model's maker.

## 4. GUI work stays with the computer-use agent
Driving a browser or desktop app, and visual QA of a UI, stays with the agent that has a screen. Do not hand it
to a terminal coding agent, and do not use a coding agent to get around a safety review.
