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

## 3. Do AI work with the box's own assistant; other vendors are an over-pace fallback
Do all AI work, coding and reviews included, with the box's own assistant bot on its subscription plan. Do not
route work to other vendors' coding TUIs or APIs (via `acp-run` or otherwise) just because they are installed.
The one exception: the chief-of-staff bot watches the assistant's plan usage against its pace for the billing
period, and when it reports usage is over pace it tells the bots that offloading is allowed until further notice.
Keep `acp-run` (see [local-bin/acp-run.README.md](../local-bin/acp-run.README.md)) installed for that fallback and
for replying to agents that message the bot.

When the fallback is in effect, choose deliberately for each task:
- **vendor and model** (match the task; do not default to the most expensive),
- **effort / reasoning level**,
- **context window** (large repos need it; small fixes do not).

After a fallback run, **verify from the agent's logs which model actually ran**. Agents can silently fall back to a
different model when a quota is hit or a flag is ignored. Record the vendor, model, and effort in the report.

Some vendors serve overlapping models (the same model family through more than one subscription or API). Count
that when choosing: the cheapest route to a model may be a different vendor than the model's maker.

## 4. GUI work stays with the computer-use agent
Driving a browser or desktop app, and visual QA of a UI, stays with the agent that has a screen. Do not hand it
to a terminal coding agent, and do not use a coding agent to get around a safety review.
