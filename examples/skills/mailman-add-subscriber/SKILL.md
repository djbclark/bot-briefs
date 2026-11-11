---
name: Mailman add subscriber
description: >-
  Use when adding or inviting someone to a GNU Mailman 2.1 list (e.g. an
  institutional Mailman host), including owner mass-subscribe, invite, or
  guiding self-subscribe.
---
# Mailman add subscriber (GNU Mailman 2.1)

Reusable recipe for MIT-style Mailman 2.1 lists. Parameterize `LIST` (short name, e.g. `<your-list>`), `EMAIL`, optional `FULL_NAME`, and mode (`subscribe` | `invite` | `self`).

## Access on MIT (verified 2026-10-01)

- MIT admin now uses **institutional single sign-on with MFA** on the `:444` host. **No client certificate** is needed anymore.
- Mass add form: `https://mailman.mit.edu:444/mailman/admin/LIST/members/add`. If a browser already has a valid admin session, it loads directly with no list password prompt.
- If the session has expired: ask the user to sign in through the sign-in / credential-request flow (the user approves any MFA prompt themselves). Never ask for the password in chat.
- The legacy list-admin-password path at `https://mailman.mit.edu/mailman/admin/LIST` may still work. Use it only as a fallback, with the password stored as a secret.

## Prior art (do not reinvent)

| Path | When | Auth | Member must confirm? |
|------|------|------|----------------------|
| **Owner Mass Subscription** (web) | Organizer adds someone for them | MIT sign-in on :444 (or list admin password) | No if mode = Subscribe |
| **Owner Invite** (same page) | Prefer consent first | Same | Yes, they must accept |
| **listinfo self-subscribe** | Person joins themselves | None | Yes, confirm email |
| **Email command** | Low-tech / no browser | Sender mailbox | Usually yes |
| **curl/GET admin URLs** ([Mailman wiki](https://wiki.list.org/DOC/Additional%20web%20administration%20tools)) | Scripted ops on password-auth hosts | `adminpw` | No for subscribe=0 |

MIT listinfo: `https://mailman.mit.edu/mailman/listinfo/LIST`
Owner alias: `LIST-owner@mit.edu` · Request robot: `LIST-request@mit.edu`
MIT runs Mailman 2.1.x (not Mailman 3 / REST). No server `bin/add_members` access; use web admin or email.

## Hard rules

1. **Confirm with the user before mutating the list.** Name the exact addresses and the mode, and get a yes first.
2. **Never put passwords in chat.** Use the credential-request flow or a masked secret.
3. Use the browser admin UI (computerUse subagent on your own desktop; read the `box-desktop` skill first). Don't use curl against the SSO-protected `:444` host.
4. Don't claim someone is already subscribed without checking. After adding, verify with a Membership List search.
5. Only touch the named list.

## Inputs to collect

- `LIST` (example: `<your-list>`)
- `EMAIL` (required); optional `FULL_NAME`, written on one line as `Full Name <email@domain>`
- Mode: **subscribe** (immediate), **invite** (consent), or **self** (just send them the listinfo link)
- Send a welcome message to this batch? (default **Yes** for newcomers)
- Notify the list owner about this batch? (default **No**)

## Path A: Owner Mass Subscription (preferred for "add them for me")

1. Open `https://mailman.mit.edu:444/mailman/admin/LIST/members/add` in the box browser.
2. If it shows an MIT sign-in, stop and handle the login through the sign-in / credential-request flow above, then continue.
3. Choose **Subscribe** or **Invite**.
4. Set the welcome-message and owner-notification options for this batch.
5. Enter one address per line, either `Full Name <email@example.com>` or a bare address.
6. Submit. Check that the result page shows success, an already-member line, or an error.
7. Open **Membership List**, search for `EMAIL`, and confirm.
8. Tell the user each address, the mode used, whether a welcome was sent, and any Mailman error text. Don't send a separate intro email unless they asked.

## Path B: Guide self-subscribe (no admin access)

1. Send the person the `https://mailman.mit.edu/mailman/listinfo/LIST` link.
2. They submit their email on that form.
3. They reply to the confirmation email (remind them to check spam).

## Path C: Email robot

Send `subscribe` (optionally `subscribe nodigest address=other@example.com`) to `LIST-request@mit.edu`. This usually still triggers a confirmation email. On a draft-never-send desk, draft it and wait for approval to send.

## Example defaults (fill in your own)

- List: `<your-list>` (`<your-list>@<list-host>`)
- Mass add: `https://<list-host>:444/mailman/admin/<your-list>/members/add`
- Listinfo: `https://<list-host>/mailman/listinfo/<your-list>`
- Owner: `<your-list>-owner@<list-host>`

## Sources

- http://web.mit.edu/lists/mailman/quickstart.html
- https://ist.mit.edu/email-lists
- https://wiki.list.org/DOC/Additional%20web%20administration%20tools
