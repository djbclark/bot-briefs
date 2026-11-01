# opencli shortwave (read-only)

Lists Shortwave Inbox **Todos** from the box Chrome's signed-in session. No public
Shortwave API exists on Pro, so the plugin reads the web app's own IndexedDB
replica (`shortwave-db-acct-<uuid>`) inside the OpenCLI adapter tab on
app.shortwave.com. It never clicks, archives, marks done or writes anything.

## Commands
    PATH=$HOME/.local/bin:$PATH
    opencli shortwave todos -f json [--limit N]   # {"ok":true,"data":[...]}
    opencli shortwave status -f json             # [{login: "yes"|"no", todos: N}]

## Output (todos)
    { title, id, link, thread_links[], date, count, messages }
- `title`: bundle_overrides.displayName (else first text of rtfDescription)
- `id`: todo (bundle) id = threads.bundleOverrideId
- `link`: https://app.shortwave.com/t/<clusterId> of the newest message of the newest thread
- `thread_links`: one link per thread in the todo (newest first)
- `date`: ISO UTC of the newest thread's latestMessageAt
- `count`: number of threads (= the badge in the Todos list)
- `messages`: total messages over those threads (thread_summaries.messageCount), null if unknown

A todo is active when at least one thread with its bundleOverrideId has inboxStatus `visible`.

## Limits
- Needs box Chrome running, signed in to app.shortwave.com, and the OpenCLI Browser
  Bridge extension connected (`opencli doctor`). Login persists in the Chrome profile;
  if it lapses, `status` reports `login: no` (wd-saner watchdog pings once).
- Data is whatever the app has synced locally; the tab loads the app if needed (≤25 s).
- Order is by sortTimestamp, not exactly the UI order. Links of threads whose messages
  are not in the local replica are null.
- Relies on undocumented IndexedDB schema; a schema change gives a clear error.
- No cache is written. Do not log output: titles are private.
