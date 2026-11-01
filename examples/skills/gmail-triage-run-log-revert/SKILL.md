---
name: Gmail triage suite
description: >-
  Use this for Gmail inbox triage and related features: dry run, run log/revert,
  waiting-on, snooze, digest reports, sender rules, e-sign radar,
  travel/money-week mode, teammate mail handoffs, and when to nudge the user.
---
# Gmail triage suite (run log, revert, waiting-on, snooze, digest, rules, e-sign, modes, handoffs)

## Standing features (every triage)
1. **Digest report** — short sections only: Urgents | Waiting-on | E-sign/unsigned | Drafts made | Archived (count) | Snoozes due today | Stats (unread start→now, runId). No wall of per-email chatter unless asked.
2. **VIP / hard-protect** — never easy-archive money, banking, taxes, legal, unsigned docs, e-sign, passwords/2FA, medical, or user-marked VIPs.
3. **Teammate handoffs** — read `/workspace/triage-state/handoffs.json` at the start of every triage and follow each entry's `zeroAction`.
   - **Full skip** handoffs (e.g. club, family, co-op owners): for matching threads (owner addresses or clear topic hints) do **not** archive, draft, label, or list them in the digest; leave them for that owner bot. Still never bulk-archive them (they remain on the protect allowlist).
   - **Partial** handoffs (e.g. a purchases/subscriptions owner that reads by search): archiving is allowed, but never trash/delete and never draft replies. When executing a bulk plan, downgrade any Delete/Kill choice on those threads to Archive (unsubscribe only with explicit user confirm, as always). Hard-protect rules above still apply; a genuine money urgent may still be flagged in the digest with a note that the owner bot has it.
   - Unrelated mail that merely shares a hobby word (e.g. a Chess News promo vs club ops) stays with this inbox bot unless it matches the handoff.
4. **Dry run** — if user says dry run / propose only, or you nudge and they haven't approved live yet: list proposed archives/drafts/flags with `dryRun: true`; mutate nothing.
5. **Run log + revert** — on live runs write `/workspace/triage-runs/<runId>.json` with actions; end with runId. On "revert last run" / "revert run <id>", reverse archives (restore inbox), delete drafts created, undo labels; mark reverted.
6. **Waiting-on** — threads where the user is owed a reply: apply existing Gmail label `Awaiting Reply` (do not invent a duplicate). List them in the digest separately from urgents. Clear the label when a reply arrives or they say drop it.
7. **Snooze-until** — apply `Zero/Snooze`, archive (or leave out of active unread), record `{threadId, untilDate YYYY-MM-DD, note}` in `/workspace/triage-state/snooze.json`. Each triage: unsnooze items with untilDate ≤ today (remove label, restore inbox if archived, remove from file). User phrases: "snooze until Monday", "bring back next week".
8. **Sender rules** — read/write `/workspace/triage-state/sender-rules.json` (`alwaysArchive`, `alwaysDraft`). On corrections ("always archive that sender"), update the file and confirm once. Apply rules automatically on later runs.
9. **E-sign / unsigned radar** — never easy-archive; apply `Zero/E-Sign`; list in digest under E-sign/unsigned. Remove label after signed or dismissed.
10. **Travel / money-week mode** — flag in `/workspace/triage-state/mode.json` (`travelMoneyWeek: true`). When on: archive almost nothing without asking; prefer dry-run proposals; tighten protect on receipts, itineraries, banking. Toggle on user ask or when Calendar clearly shows travel / tax / closing / large payment window — and **nudge** before flipping on yourself.

## Nudges (brief, one at a time, easy to wave off)
Offer the feature when the signal appears — don't nag:
- Huge unread backlog or first live run after setup → suggest dry run
- Calendar shows travel, tax deadline, closing, large payment → suggest travel/money-week mode
- Same sender archived or drafted 2–3 times by correction → offer a sender rule
- Waiting-on pile grows stale (>7 days) → nudge the list
- Unsigned / DocuSign / HelloSign / Adobe Sign appears → call out e-sign radar (already labeling)
- User says "later" / "not this week" about a thread → offer snooze-until
- After a live run with many archives → remind revert last run is available

## Hard never
Never send, never unsubscribe without asking, never click sketchy links/attachments, never mark personal mail spam, never delete without explicit ask, never trash anything a partial handoff owns.

## Limits on revert
Cannot unsend; cannot restore user-deleted mail outside the run. Prefer archive over trash.
