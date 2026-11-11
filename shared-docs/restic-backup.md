# restic backup of the box → Google Drive (trial, runs alongside box-backup.sh)

Status (2026-10-21): tools installed, script written and smoke-tested against a throwaway local repo.
**Blocked: rclone has no Google Drive OAuth token yet.** The existing nightly backup is untouched:
/home/box/bin/box-backup.sh, Drive folder "Grok Bot backups" (<FOLDER_ID_BACKUPS>).

- Binaries: ~/.local/bin/restic (0.19.1), ~/.local/bin/rclone (v1.75.2)
- Repo: `rclone:gdrive:Grok Bot restic` (rclone creates the folder on `restic init`)
- Password: env BOX_BACKUP_PASSPHRASE via RESTIC_PASSWORD_COMMAND='printenv BOX_BACKUP_PASSPHRASE'.
  LOSE IT = LOSE THE REPO.
- Script: /home/box/bin/box-restic-backup.sh. It backs up, runs forget --keep-daily 14 --keep-weekly 8 --prune,
  and prints JSON (snapshot_id, data_added_bytes, data_added_packed_bytes, repo_total_size_bytes, …).
- Coverage: the union of box-backup.sh's plain and secret sets with the same excludes, and SQLite DBs
  (sand-data, .beeper/profiles) taken as online-backup snapshots in a private mktemp dir
  (/tmp/box-restic-stage.XXXXXXXX/dbsnap). Because that path changes each run, backup and forget both use
  --group-by host,tags so parent-snapshot matching and retention still work. /workspace files that match credential patterns are NOT excluded
  here (they were excluded from the old plain archive only because it was unencrypted).

## Remaining step (operator)
1. On the Mac: `brew install rclone` then `rclone authorize "drive"`, sign in as the Google account, and copy the
   JSON token it prints ({"access_token":...,"refresh_token":...}).
2. Paste it through a secret request (box env secret, e.g. RCLONE_GDRIVE_TOKEN). Never paste it in chat.
3. The agent then runs:
   `umask 077; printf '[gdrive]\ntype = drive\nscope = drive\ntoken = %s\n' "$RCLONE_GDRIVE_TOKEN" >> ~/.config/rclone/rclone.conf`
   (writes the token into the 0600 rclone config via a shell builtin, so it never appears in any process's argv;
   avoid `rclone config create ... token="$TOKEN"`, which puts it on the command line), then `rclone lsd gdrive:`, `restic init`, two backups,
   `restic check`, a restore test, and adds a nightly cron/routine.

## Smoke test (local repo /tmp, since deleted)
first backup 448 MB processed → 125 MB packed; unchanged re-run added ~0.13 MB; check: no errors; restore of
~/bin + ~/.bashrc: byte-identical.

## Restore
`restic -r "rclone:gdrive:Grok Bot restic" restore latest --target / [--include PATH]`

## Security notes (repo version)
- The old `box-backup.sh` "plain" archive is **not encrypted**. Retire it in favour of restic (which encrypts
  everything), or set `BOX_BACKUP_ENCRYPT_PLAIN=1` so it is gpg-encrypted like the secrets archive.
- Give gpg and restic a **dedicated passphrase file** readable only by the backup job's user
  (`chmod 600`; `BOX_BACKUP_PASSPHRASE_FILE`, `RESTIC_PASSWORD_FILE`), not an env var that every agent on the box
  inherits. Use different passphrases for the two tools, and keep a copy offline: lose it = lose the backups.
- `box-restic-backup.sh` uses `mktemp -d` for its staging dir and a private lock dir instead of fixed `/tmp` paths.
- `SECRET_RE` in `box-backup.sh` also catches Telegram bot tokens, Slack/Discord webhook URLs, webhook keys and
  generic Bearer/token/Basic auth headers.
