# Zero-token pollers (wake a bot only on change)

A bot routine that wakes every 15 minutes to check "is there anything new?" spends model tokens on every run,
even when the answer is no. Most of those checks are deterministic: a file appeared, a git ref moved, a log line
was added, a service stopped answering. Move them into a small script that monit runs on a timer, and let the
script wake the bot **only** when it finds a change. Polling then costs zero LLM tokens and can run as often as
you like.

Example script: [`watchdogs/bin/wd-example-poller`](../watchdogs/bin/wd-example-poller).
Example monit drop-in: [`watchdogs/conf.d/example-pollers.monitrc.disabled`](../watchdogs/conf.d/example-pollers.monitrc.disabled).

## Which routines can move
- **Yes:** anything a script can decide alone: drop folders, mailbox directories, `git fetch` + compare refs,
  log files, HTTP health checks, "did the box restart" (`/proc/sys/kernel/random/boot_id`, uptime), "is a listener
  still up" (`ss -ltn`), "does a config file still have the expected fingerprint".
- **No:** checks that need judgment ("is this email about X?") or credentials that only live in a cloud connector.
  Keep those as LLM routines, and run them only a few hours before the report they feed (see
  [`docs/token-saving-rules.md`](../docs/token-saving-rules.md)).

## The pattern
1. **Lock file** (`flock`, non-blocking): a slow run never overlaps the next one.
2. **State file** (JSON, written to a temp file then renamed): the keys already seen. Use stable keys, such as
   `name:mtime:size` for files or a message id for chats.
3. **Change detection:** current keys minus seen keys. Advance state only after the wake succeeds, so a failed
   send is retried on the next run.
4. **Wake on change:** one call to `notify-bot <bot> -f <script> -s <subject> "<message>"`, or a POST to
   `$WEBHOOK_URL` with the URL and auth header passed to curl on stdin (`curl -K -`), never in argv.
5. **Untrusted data:** filenames, senders, subjects and previews come from outside. Strip control characters,
   collapse whitespace, truncate, JSON-quote, and put them inside an explicit `UNTRUSTED_DATA_BEGIN/END` block.
   Tell the bot to open the source itself; the wake text is a pointer, not instructions.
6. **Dry run:** `--dry-run` prints the wake and sends nothing; `--dry-run-save` also writes state, which is how
   you seed a baseline so the first live run does not re-announce old items.
7. **Exit status:** 0 when the poller worked (change or not), non-zero only when the poller itself is broken, so
   monit alerts mean "fix the poller", not "there is news".
8. **Quiet windows:** skip network calls during known maintenance or reboot windows.
9. **Outage alerts:** if a source (an API, a chat client) fails for a long time, send one alert and remember
   that you sent it, rather than one per run.

## Cutover from an LLM routine
1. Write the script. List exactly what the routine checked and reproduce each check.
2. `wd-<name> --dry-run` against today's data. It should find the same items the routine would.
   Compare with the routine's last few runs; a script often finds items the routine missed.
3. `wd-<name> --dry-run-save` to seed the baseline at "everything seen so far".
4. `wd-<name> --dry-run` again: expect `NO CHANGE detected`.
5. Enable monit (below) and drop a test item; confirm exactly one wake arrives.
6. Only then pause or delete the bot's LLM polling routine. Keep the routine text so you can restore it.

### Rollback
1. Recreate or resume the bot's LLM routine.
2. `monit ... unmonitor <check>` (pause), or rename the drop-in back to `.disabled` and reload (remove).
3. Keep the state file; it is harmless and lets you re-enable later without re-seeding.

## Monit commands
Monit only includes `conf.d/*.monitrc`, so a `*.monitrc.disabled` file is installed but inactive.

```bash
M="monit -c ~/.config/monit/monitrc"
$M -t                                     # syntax check (read-only)
wd-example-poller --dry-run-save          # seed state, no wake
mv conf.d/example-pollers.monitrc.disabled conf.d/example-pollers.monitrc
$M -t && $M reload                        # enable
$M status example-poller
$M unmonitor example-poller               # pause
$M monitor example-poller                 # resume
mv conf.d/example-pollers.monitrc conf.d/example-pollers.monitrc.disabled && $M reload   # disable
```

With `set daemon 30`, `every 10 cycles` = 5 minutes and `every 30 cycles` = 15 minutes.
