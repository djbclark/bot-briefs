#!/bin/bash
# EXAMPLE, DO NOT USE AS-IS. Idempotent: recreate a `coding` ssh drop account, its drop dirs and the sshd allowance
# on the box (run as box with sudo -n). The box's platform may reset /etc and /home/<user> on restart; rerun then.
# The account is key-only, has no shell, and is confined to SFTP inside /srv/coding-drop (chroot) on a separate
# guest sshd, never the main one. Fill in: CODING_DROP_PUBKEY (one public key line from your other machine),
# optionally CODING_DROP_FROM (source address pattern for the key, e.g. your tailnet range), SSHD_CONF, SSHD_PIDFILE.
set -euo pipefail
PUBKEY=${CODING_DROP_PUBKEY:?set CODING_DROP_PUBKEY to the ssh public key line for the drop account}
FROM=${CODING_DROP_FROM:-}
SSHD_CONF=${SSHD_CONF:-/etc/ssh/sshd_guest.conf}
PIDFILE=${SSHD_PIDFILE:-/run/sshd_guest.pid}
BOT=$(id -un)
case "$PUBKEY" in ssh-ed25519\ *|ssh-rsa\ *|ecdsa-sha2-*) ;; *) echo "not an ssh public key" >&2; exit 2 ;; esac
case "$PUBKEY" in *$'\n'*|*\"*) echo "one plain key line only" >&2; exit 2 ;; esac
case "$FROM" in *[!0-9A-Za-z.:/*?,-]*) echo "bad CODING_DROP_FROM" >&2; exit 2 ;; esac
sudo -n test -f "$SSHD_CONF" || { echo "no $SSHD_CONF" >&2; exit 2; }
sudo -n grep -q '^AllowUsers ' "$SSHD_CONF" || { echo "$SSHD_CONF has no AllowUsers line; refusing (would allow everyone)" >&2; exit 2; }

id coding >/dev/null 2>&1 || sudo -n useradd -m -s /usr/sbin/nologin -U coding
sudo -n usermod -s /usr/sbin/nologin -p '*' coding    # no shell, no password
sudo -n install -d -m 0700 -o coding -g coding /home/coding/.ssh
opts=restrict; [ -n "$FROM" ] && opts="restrict,from=\"$FROM\""
printf '%s %s\n' "$opts" "$PUBKEY" | sudo -n tee /home/coding/.ssh/authorized_keys >/dev/null
sudo -n chown coding:coding /home/coding/.ssh/authorized_keys; sudo -n chmod 600 /home/coding/.ssh/authorized_keys

# chroot root must be root-owned and not group/world-writable; subdirs are private to the two parties
sudo -n install -d -m 0755 -o root -g root /srv /srv/coding-drop
sudo -n install -d -m 0750 -o coding -g "$BOT" /srv/coding-drop/to-coding     # coding writes, bot reads
sudo -n install -d -m 0750 -o "$BOT" -g coding /srv/coding-drop/from-coding   # bot writes, coding reads

if ! sudo -n grep -qE '^AllowUsers(.* )?coding( |$)' "$SSHD_CONF"; then
  sudo -n sed -i -E 's/^(AllowUsers .*)$/\1 coding/' "$SSHD_CONF"
fi
if ! sudo -n grep -q '^Match User coding$' "$SSHD_CONF"; then
  printf '\nMatch User coding\n    ForceCommand internal-sftp\n    ChrootDirectory /srv/coding-drop\n    PermitTTY no\n    AllowTcpForwarding no\n    X11Forwarding no\n    AllowAgentForwarding no\n' |
    sudo -n tee -a "$SSHD_CONF" >/dev/null
fi
sudo -n /usr/sbin/sshd -t -f "$SSHD_CONF"
[ -f "$PIDFILE" ] && sudo -n kill -HUP "$(cat "$PIDFILE")" || true
