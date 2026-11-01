# Phase 0 interview script

The CoS runs this BEFORE personalizing anything. Ask 3-5 questions at a time; Daniel's answers are shown as examples only. Record answers in [config/personal-profile.template.md](../config/personal-profile.template.md) format and save to memory.

````text
INTERVIEW SCRIPT (Phase 0). Ask in batches of 3-5, record answers in memory, summarize back, then personalize.
Offer Daniel's answer as an example where shown.
A. YOU AND YOUR TIME
 1. Name (what bots call you)? Timezone? (Daniel: Dan, America/New_York)
 2. Quiet hours? When should the daily list arrive? (Daniel: bots 3:14 AM, CoS list 6:16 AM)
 3. Weekly review day? (Daniel: Friday)
 4. Reached how: Grok Bot chat only, or also Telegram/SMS/email for urgent alerts? (Daniel: chat; watchdog alerts via Hermes->Telegram)
B. THE BOTS
 5. Which areas need a bot? Daniel's: Radar (work GitHub+Jira+mail), Zero (Gmail inbox zero), Family, Money, Business
    (small home business), Chess (club), Pika (housing co-op), Friends, Techno Euphora (side project), One-offs, Coding, Other (catch-all TODOs).
 6. Per bot: scope, sources, never-do rules, schedule (see examples/bots/<slug>.md).
 7. May bots ever send on your behalf? (Daniel: never without his approval card; bots draft.)
C. ACCOUNTS AND CONNECTORS
 8. Email accounts? Primary? (Daniel: 3 Gmail accounts)
 9. Calendars, Drive, Slack, Jira/Confluence, GitHub/GitLab/Forgejo accounts and orgs?
10. Chat apps (Beeper bridging WhatsApp/Signal/SMS/Discord?)
11. TODO/notes sources: Saner.ai, Shortwave, Todoist, Things, Notion, Linear...? Read-only?
12. Finance connector / Money bot?
D. INFRASTRUCTURE
13. Tailscale tailnet + login email (needed for the private dashboard URL)?
14. VPS for the optional mirror? OS? (Daniel: NixOS)
15. An often-on Mac/PC for the Grok Bot computer connector (fallback routes)?
16. OK to back up the box to your Google Drive with restic (you create and keep the passphrase)?
E. OTHER AI AGENTS
17. Which do you have: Claude Code, Antigravity (agy), Cursor, Cline, OpenRouter, Hermes, Codex? Default offload agent?
    (Daniel: agy by default, Claude Code --effort high for complex builds/reviews.)
F. RULES AND PRIVACY
18. Anything bots must never read or repeat?
19. A pinned top item in the daily list? (Most people: none.)
20. Ref lines on every post (`Ref: BOT-1a`)? (Recommended.)
21. Tone/length, max items, "N lower-priority items not shown"?
22. What must a bot tell you immediately (connector disconnected, source failing twice)?
Finish: write a one-screen setup profile, get OK, save to memory, start phase 1.
````
