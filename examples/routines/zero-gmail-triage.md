# Zero (inbox zero bot): stored routine prompts

> **EXAMPLE TEMPLATE, DO NOT USE AS-IS.** CoS: show this to the user as an example, ask the interview questions below it, then write THEIR version with them and get their OK before saving it anywhere.

````text
=== Routine 'Afternoon triage'  schedule (cron, user tz): 0 16 * * 1-5 ===
Run weekday afternoon Gmail triage for <USER_NAME> (<TIMEZONE>) using the Gmail triage suite skill.

Flow: note unread at start; check Calendar only for due-today urgency and for travel/money-week nudge signals; process snoozes due today from Zero/Snooze + snooze.json; apply sender-rules.json; archive clear noise only (prefer archive); draft replies as Gmail drafts never send; label waiting-on with Awaiting Reply; label unsigned/e-sign with Zero/E-Sign; respect VIPs (the <VIP_LIST> from the user's private config, never hardcoded here) and hard-protect money/legal/e-sign/2FA/medical.

If travelMoneyWeek is on in mode.json, barely archive — propose instead. Write live run log under /workspace/triage-runs and give runId. Digest report only: Urgents | Waiting-on | E-sign | Drafts | Archived count | Snoozes due | unread start→now + runId. If unread barely moved, say what blocked you. Nudge at most one useful feature if a clear signal appears. Never send, unsubscribe without ask, click sketchy links, mark personal spam, or delete without ask.

=== Routine 'Morning triage'  schedule (cron, user tz): 0 8 * * 1-5 ===
Run weekday morning Gmail triage for <USER_NAME> (<TIMEZONE>) using the Gmail triage suite skill.

Flow: note unread at start; check Calendar only for due-today urgency and for travel/money-week nudge signals; process snoozes due today from Zero/Snooze + snooze.json; apply sender-rules.json; archive clear noise only (prefer archive); draft replies as Gmail drafts never send; label waiting-on with Awaiting Reply; label unsigned/e-sign with Zero/E-Sign; respect VIPs (the <VIP_LIST> from the user's private config, never hardcoded here) and hard-protect money/legal/e-sign/2FA/medical.

If travelMoneyWeek is on in mode.json, barely archive — propose instead. Write live run log under /workspace/triage-runs and give runId. Digest report only: Urgents | Waiting-on | E-sign | Drafts | Archived count | Snoozes due | unread start→now + runId. If unread barely moved, say what blocked you. Nudge at most one useful feature if a clear signal appears. Never send, unsubscribe without ask, click sketchy links, mark personal spam, or delete without ask.
````

## Interview questions

1. Do you want an inbox-zero bot? Which Gmail account(s)?
2. Times (e.g. 08:00 and 16:00 weekdays)?
3. Which labels/archive rules? Never delete, never send: confirm.
4. Keep a run log so a triage run can be reverted (see [skill](../skills/gmail-triage-run-log-revert/SKILL.md))?
