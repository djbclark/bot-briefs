# Phase 12: Backups (restic + rclone -> Google Drive)

[Back to README](../README.md) | Previous: [phase-11-watchdogs.md](phase-11-watchdogs.md) | Next: [phase-13-vps-mirror.md](phase-13-vps-mirror.md)

## How the CoS runs this phase

1. Explain in 2-4 sentences what this phase does and why. Mark what is Grok-Bot-specific vs generic.
2. Show the relevant repo files/examples. Anything marked `EXAMPLE TEMPLATE, DO NOT USE AS-IS`: show as an example, ask the interview questions, draft the user's own version, get an explicit OK.
3. Ask before running anything that installs, overwrites, sends, spends, exposes a service, or is destructive.
4. Do the steps. Prefer `scripts/install.sh --phase 12` (dry run) then `--apply` for file copies.
5. Run the Verify section and report results honestly.
6. Record progress (PROGRESS.md + memory) and wait for the user before the next phase.

## Guide (from the operator's working system)

```text
 1. restic + rclone in ~/.local/bin (scripts/install-deps.sh).
 2. rclone Drive token: on a machine with a browser run `rclone authorize "drive"`, sign in, copy the JSON token; store as box
    secret RCLONE_GDRIVE_TOKEN (never chat). `rclone config create gdrive drive scope=drive token="$RCLONE_GDRIVE_TOKEN" --non-interactive; rclone lsd gdrive:`
 3. Passphrase: user creates one (`openssl rand -base64 33`), saves in password manager AND as box secret BOX_BACKUP_PASSPHRASE.
    Lose it = lose the backups. `export RESTIC_PASSWORD_COMMAND='printenv BOX_BACKUP_PASSPHRASE'`
 4. `restic -r "rclone:gdrive:Grok Bot restic" init`; the files listed under 'Repo files' below (`scripts/install.sh --phase N`); run box-restic-backup.sh; `restic check`;
    test restore to /tmp.
 5. Schedule (no cron): a routine at 3:14 AM on CoS or Coding: "run /home/box/bin/box-restic-backup.sh; report only failures".
Retention: --keep-daily 14 --keep-weekly 8 --prune. SQLite snapshotted via backup API first. box-backup.sh = older tar backup.
Restore: `restic -r "rclone:gdrive:Grok Bot restic" restore latest --target / --include <path>`.  Verify: `scripts/verify.sh 12`.
```

## Notes

Doc: [shared-docs/restic-backup.md](../shared-docs/restic-backup.md). The passphrase and rclone token are created by the USER and stored as box secrets; never in chat, never in the repo.

## Repo files and where they go (2 files)

Copy with `scripts/install.sh --phase 12` (dry run; add `--apply` to write; existing files are kept unless `--force`, which makes a `.bak-<timestamp>` first). Manual equivalent: `install -D -m <mode> <repo_path> <destination>`.

| repo path | destination | mode |
|---|---|---|
| [`bin/box-backup.sh`](../bin/box-backup.sh) | `/home/box/bin/box-backup.sh` | 755 |
| [`bin/box-restic-backup.sh`](../bin/box-restic-backup.sh) | `/home/box/bin/box-restic-backup.sh` | 755 |

## Verify

```bash
scripts/verify.sh 12
```

Done when the checks pass and the user agrees. Then update PROGRESS.md: `phase 12 done <date>; next 13`.
