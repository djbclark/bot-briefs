from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from typing import Optional

from briefs.config import bot_cli_name, now_et

_NOTIFY_CACHE: dict[str, tuple[float, bool]] = {}


def clear_notify_cache() -> None:
    """Clear the cached notify_configured results."""
    _NOTIFY_CACHE.clear()


def get_notify_cmd() -> str:
    """Return the notify-bot executable path."""
    return os.environ.get("BRIEFS_NOTIFY_CMD", "/home/box/.local/bin/notify-bot")


def _notify_argv(*args: str) -> list[str]:
    """Build argv for notify subprocess (use sys.executable for .py helpers in Nix/tests)."""
    cmd = get_notify_cmd()
    if cmd.endswith(".py"):
        return [sys.executable, cmd, *args]
    return [cmd, *args]


def notify_configured(cli_name: str, use_cache: bool = True) -> bool:
    """Check if notify-bot has the bot configured. Caches results ~60 seconds."""
    now = time.monotonic()
    if use_cache and cli_name in _NOTIFY_CACHE:
        exp, val = _NOTIFY_CACHE[cli_name]
        if now < exp:
            return val

    notify_cmd = get_notify_cmd()
    try:
        proc = subprocess.run(
            _notify_argv("--list"),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if proc.returncode != 0:
            return False

        found_val = False
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if not parts:
                continue
            name = parts[0]
            is_yes = any(p == "configured=yes" for p in parts[1:]) or "configured=yes" in line
            _NOTIFY_CACHE[name] = (now + 60.0, is_yes)
            if name == cli_name:
                found_val = is_yes

        return found_val
    except Exception:
        return False


def dispatch_configured(use_cache: bool = True) -> bool:
    """Check if chief-of-staff is configured in notify-bot."""
    return notify_configured("chief-of-staff", use_cache=use_cache)


def trigger_dispatch(db_path: str, action_ids: list[int]) -> str:
    """Run notify-bot for chief-of-staff on submitted action_ids and record wake_status on them."""
    if not action_ids:
        return "no actions"

    now_str = now_et().isoformat()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        is_conf = dispatch_configured(use_cache=True)
        if not is_conf:
            wake_status = "dispatch not configured"
            with conn:
                ph = ",".join("?" * len(action_ids))
                conn.execute(
                    f"UPDATE actions SET wake_status = ?, updated_at = ? WHERE id IN ({ph})",
                    [wake_status, now_str] + action_ids,
                )
            return wake_status

        n = len(action_ids)
        msg = "1 action submitted" if n == 1 else f"{n} actions submitted"
        notify_cmd = get_notify_cmd()

        try:
            proc = subprocess.run(
                _notify_argv(
                    "chief-of-staff",
                    "-f",
                    "dashboard",
                    "-m",
                    "none",
                    "-s",
                    "Dashboard submit",
                    msg,
                ),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
            if proc.returncode == 0:
                wake_status = "dispatch sent"
            else:
                wake_status = f"dispatch failed (exit {proc.returncode})"
        except Exception:
            wake_status = "dispatch failed"

        with conn:
            ph = ",".join("?" * len(action_ids))
            conn.execute(
                f"UPDATE actions SET wake_status = ?, updated_at = ? WHERE id IN ({ph})",
                [wake_status, now_str] + action_ids,
            )
        return wake_status
    finally:
        conn.close()


def wake_bot(conn: sqlite3.Connection, action_id: int) -> str:
    """
    Wake the bot for a given action.
    Updates actions.wake_status to 'not_configured', 'sent', or 'failed (exit N)'.
    Never stores or logs stdout/stderr secrets.
    """
    row = conn.execute(
        """
        SELECT a.id, a.type, a.bot_id, i.title
        FROM actions a
        JOIN items i ON i.id = a.item_id
        WHERE a.id = ?
        """,
        (action_id,),
    ).fetchone()

    if not row:
        return "not_found"

    cli_name = bot_cli_name(row["bot_id"])
    title = (row["title"] or "")[:80]
    act_type = row["type"]

    is_conf = notify_configured(cli_name, use_cache=True)
    now_str = now_et().isoformat()

    if not is_conf:
        wake_status = "not_configured"
        with conn:
            conn.execute(
                "UPDATE actions SET wake_status = ?, updated_at = ? WHERE id = ?",
                (wake_status, now_str, action_id),
            )
        return wake_status

    notify_cmd = get_notify_cmd()
    message = (
        f'Daniel left a dashboard action for you: #{action_id} ({act_type}) on "{title}". '
        f"Run `brief inbox --bot {cli_name} --json`, handle it under your normal rules, "
        f'then `brief ack {action_id} --status applied|failed --note "..."`.'
    )

    try:
        proc = subprocess.run(
            _notify_argv(
                cli_name,
                "-f",
                "dashboard",
                "-m",
                "none",
                "-s",
                f"Dashboard action #{action_id}",
                message,
            ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
        if proc.returncode == 0:
            wake_status = "sent"
        else:
            wake_status = f"failed (exit {proc.returncode})"
    except Exception:
        wake_status = "failed"

    with conn:
        conn.execute(
            "UPDATE actions SET wake_status = ?, updated_at = ? WHERE id = ?",
            (wake_status, now_str, action_id),
        )
    return wake_status
