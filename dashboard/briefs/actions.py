from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, Optional

from briefs.config import (
    BRIEFS_PUBLIC_URL,
    Bot,
    bot_cli_name,
    find_bot,
    now_et,
    today_et,
)

VALID_ACTION_TYPES = {"done", "dismiss", "snooze", "priority", "reply", "reopen"}
VALID_STATUSES = {"queued", "submitted", "processing", "applied", "discuss", "failed", "pending", "seen"}


def create_action(
    conn: sqlite3.Connection,
    item_id: int,
    type: str,
    payload: dict[str, Any],
    author: str,
    status: Optional[str] = None,
) -> dict[str, Any]:
    """Create an action and apply its local effect immediately for icon actions."""
    if type not in VALID_ACTION_TYPES:
        raise ValueError(
            f"Invalid action type '{type}'. Allowed types: {', '.join(sorted(VALID_ACTION_TYPES))}"
        )

    item = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        raise ValueError(f"Item not found: {item_id}")

    # Validate payload and determine action status
    until: Optional[str] = None
    new_priority: Optional[int] = None

    if type in ("done", "dismiss", "snooze", "priority", "reopen"):
        # Icon actions always have status 'pending'
        action_status = "pending"
        if type == "snooze":
            raw_until = payload.get("until")
            if not raw_until or not isinstance(raw_until, str):
                raise ValueError("Snooze action requires 'until' date string in payload (YYYY-MM-DD)")
            until = raw_until.strip()
            try:
                datetime.strptime(until, "%Y-%m-%d").date()
            except ValueError:
                raise ValueError(f"Invalid snooze date format '{until}'. Expected YYYY-MM-DD")
            curr_today = today_et()
            if until <= curr_today:
                raise ValueError(f"Snooze date ({until}) must be in the future (after today ET {curr_today})")
        elif type == "priority":
            delta = payload.get("delta")
            if delta not in (-1, 1):
                raise ValueError("Priority action requires delta of -1 (more important) or +1 (less important)")
            curr_p = item["priority"] if item["priority"] is not None else 3
            new_priority = max(1, min(5, curr_p + delta))
    elif type == "reply":
        # Text actions must be 'queued' or 'submitted'
        text = payload.get("text")
        if not text or not isinstance(text, str) or not text.strip():
            raise ValueError("Reply action requires non-empty 'text' in payload")
        if len(text) > 4000:
            raise ValueError(f"Reply text exceeds maximum length of 4000 characters (got {len(text)})")
        if status is None:
            action_status = "queued"
        elif status in ("queued", "submitted"):
            action_status = status
        else:
            raise ValueError(f"Reply action status must be 'queued' or 'submitted', got '{status}'")
    else:
        action_status = "pending"

    wake_requested = 1 if payload.get("wake") else 0
    wake_status = ""
    now_str = now_et().isoformat()
    bot_id = item["bot_id"]
    payload_json = json.dumps(payload)

    with conn:
        cur = conn.execute(
            """
            INSERT INTO actions (
                item_id, bot_id, type, payload_json, author, status,
                wake_requested, wake_status, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                bot_id,
                type,
                payload_json,
                author or "unknown",
                action_status,
                wake_requested,
                wake_status,
                now_str,
                now_str,
            ),
        )
        action_id = cur.lastrowid

        # Apply local effects immediately for icon actions
        if type == "done":
            conn.execute("UPDATE items SET status = 'done' WHERE id = ?", (item_id,))
        elif type == "dismiss":
            conn.execute("UPDATE items SET status = 'dismissed' WHERE id = ?", (item_id,))
        elif type == "snooze":
            conn.execute("UPDATE items SET snooze_until = ? WHERE id = ?", (until, item_id))
        elif type == "priority":
            conn.execute("UPDATE items SET priority = ? WHERE id = ?", (new_priority, item_id))
        elif type == "reopen":
            conn.execute("UPDATE items SET status = 'open', snooze_until = NULL WHERE id = ?", (item_id,))

    action_row = conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
    res = dict(action_row)
    res["payload"] = json.loads(res.get("payload_json") or "{}")
    return res


def importance_key(conn: sqlite3.Connection, item_id: int) -> tuple:
    """Sort key for item actions, smaller = more important:
    (0 if the item is in the latest Chief of Staff report else 1,
     its position in that CoS report (else 0),
     priority if not NULL else 3,
     due_at if set else '9999-12-31',
     0 if the item's latest report_item flags contain NEW else 1,
     first_seen (older first),
     item_id).
    """
    cos_bot = find_bot("cos")
    cos_rep_id = None
    if cos_bot:
        rep_row = conn.execute(
            """
            SELECT id FROM reports
            WHERE bot_id = ?
            ORDER BY report_date DESC, id DESC
            LIMIT 1
            """,
            (cos_bot.id,),
        ).fetchone()
        if rep_row:
            cos_rep_id = rep_row["id"]

    in_cos = 1
    cos_pos = 0
    if cos_rep_id:
        ri_cos = conn.execute(
            "SELECT position FROM report_items WHERE report_id = ? AND item_id = ?",
            (cos_rep_id, item_id),
        ).fetchone()
        if ri_cos:
            in_cos = 0
            cos_pos = ri_cos["position"] or 0

    item_row = conn.execute("SELECT priority, due_at, first_seen FROM items WHERE id = ?", (item_id,)).fetchone()
    if item_row:
        prio = item_row["priority"] if item_row["priority"] is not None else 3
        due = item_row["due_at"] if item_row["due_at"] else "9999-12-31"
        fs = item_row["first_seen"] or ""
    else:
        prio = 3
        due = "9999-12-31"
        fs = ""

    ri_flags = conn.execute(
        """
        SELECT ri.flags FROM report_items ri
        JOIN reports r ON r.id = ri.report_id
        WHERE ri.item_id = ?
        ORDER BY r.report_date DESC, r.id DESC, ri.position ASC
        LIMIT 1
        """,
        (item_id,),
    ).fetchone()
    flags_list: list[str] = []
    if ri_flags and ri_flags["flags"]:
        flags_list = [f.strip() for f in ri_flags["flags"].split(",") if f.strip()]
    is_new = 0 if "NEW" in flags_list else 1

    return (in_cos, cos_pos, prio, due, is_new, fs, item_id)


def rerank_submitted(conn: sqlite3.Connection) -> None:
    """Assign rank = 1..N over ALL actions with status 'submitted',
    ordered by importance_key of their item then created_at, id.
    """
    rows = conn.execute("SELECT id, item_id, created_at FROM actions WHERE status = 'submitted'").fetchall()
    if not rows:
        return

    def _sort_key(r: sqlite3.Row) -> tuple:
        iid = r["item_id"]
        ik = importance_key(conn, iid) if iid else (1, 0, 3, "9999-12-31", 1, "", 0)
        return (ik, r["created_at"] or "", r["id"])

    sorted_rows = sorted(rows, key=_sort_key)
    with conn:
        for rank_num, r in enumerate(sorted_rows, start=1):
            conn.execute("UPDATE actions SET rank = ? WHERE id = ?", (rank_num, r["id"]))


def submit_text(conn: sqlite3.Connection, item_id: int, text: str, author: str) -> dict[str, Any]:
    """Create 'submitted' reply action, rerank, and return it."""
    act = create_action(conn, item_id, "reply", {"text": text}, author, status="submitted")
    rerank_submitted(conn)
    row = conn.execute("SELECT * FROM actions WHERE id = ?", (act["id"],)).fetchone()
    res = dict(row)
    res["payload"] = json.loads(res.get("payload_json") or "{}")
    return res


def queue_text(conn: sqlite3.Connection, item_id: int, text: str, author: str) -> dict[str, Any]:
    """Create 'queued' reply action."""
    return create_action(conn, item_id, "reply", {"text": text}, author, status="queued")


def submit_all(conn: sqlite3.Connection, drafts: list[dict[str, Any]], author: str) -> list[dict[str, Any]]:
    """In ONE transaction create a 'submitted' reply action per non-empty draft,
    flip every 'queued' action to 'submitted', rerank; return the submitted actions
    of this batch ordered by rank.
    """
    valid_drafts: list[tuple[int, str]] = []
    for d in drafts:
        text = (d.get("text") or "").strip()
        if not text:
            continue
        iid = d.get("item_id")
        if iid is None:
            raise ValueError("Draft missing item_id")
        try:
            iid_int = int(iid)
        except (ValueError, TypeError):
            raise ValueError(f"Invalid item id: {iid}")
        item_row = conn.execute("SELECT id FROM items WHERE id = ?", (iid_int,)).fetchone()
        if not item_row:
            raise ValueError(f"Unknown item id: {iid_int}")
        if len(text) > 4000:
            raise ValueError(f"Reply text exceeds maximum length of 4000 characters (got {len(text)})")
        valid_drafts.append((iid_int, text))

    batch_ids: list[int] = []
    now_str = now_et().isoformat()

    with conn:
        for iid_int, text in valid_drafts:
            item_row = conn.execute("SELECT bot_id FROM items WHERE id = ?", (iid_int,)).fetchone()
            cur = conn.execute(
                """
                INSERT INTO actions (
                    item_id, bot_id, type, payload_json, author, status,
                    wake_requested, wake_status, created_at, updated_at
                )
                VALUES (?, ?, 'reply', ?, ?, 'submitted', 0, '', ?, ?)
                """,
                (iid_int, item_row["bot_id"], json.dumps({"text": text}), author or "unknown", now_str, now_str),
            )
            batch_ids.append(cur.lastrowid)

        queued_rows = conn.execute("SELECT id FROM actions WHERE status = 'queued'").fetchall()
        for qr in queued_rows:
            batch_ids.append(qr["id"])

        if queued_rows:
            q_ids = [qr["id"] for qr in queued_rows]
            ph = ",".join("?" * len(q_ids))
            conn.execute(
                f"UPDATE actions SET status = 'submitted', updated_at = ? WHERE id IN ({ph})",
                [now_str] + q_ids,
            )

        rerank_submitted(conn)

    if not batch_ids:
        return []

    ph = ",".join("?" * len(batch_ids))
    rows = conn.execute(
        f"SELECT * FROM actions WHERE id IN ({ph}) ORDER BY rank ASC NULLS LAST, created_at ASC, id ASC",
        batch_ids,
    ).fetchall()

    res = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d.get("payload_json") or "{}")
        res.append(d)
    return res


def mark(conn: sqlite3.Connection, action_id: int, status: str) -> dict[str, Any]:
    """Only 'processing' allowed for now (from queued/submitted/pending/seen/discuss); sets updated_at."""
    if status != "processing":
        raise ValueError(f"Invalid status '{status}'. Only 'processing' is allowed")

    action_row = conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
    if not action_row:
        raise ValueError(f"Action not found: {action_id}")

    curr_st = action_row["status"]
    if curr_st not in ("queued", "submitted", "pending", "seen", "discuss"):
        raise ValueError(f"Cannot mark action from status '{curr_st}' to '{status}'")

    now_str = now_et().isoformat()
    with conn:
        conn.execute(
            "UPDATE actions SET status = 'processing', updated_at = ? WHERE id = ?",
            (now_str, action_id),
        )

    updated = conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
    res = dict(updated)
    res["payload"] = json.loads(res.get("payload_json") or "{}")
    return res


def ack(
    conn: sqlite3.Connection,
    action_id: int,
    status: str,
    note: Optional[str] = None,
    keep_open: bool = False,
) -> dict[str, Any]:
    """Acknowledge an action as applied, failed, or discuss, saving an optional bot note."""
    if status not in ("applied", "failed", "discuss"):
        raise ValueError(f"Invalid ack status '{status}'. Must be 'applied', 'failed', or 'discuss'")

    note_str = (note or "").strip()
    if len(note_str) > 2000:
        raise ValueError(f"Note exceeds maximum length of 2000 characters (got {len(note_str)})")

    action_row = conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
    if not action_row:
        raise ValueError(f"Action not found: {action_id}")

    now_str = now_et().isoformat()
    with conn:
        conn.execute(
            """
            UPDATE actions
            SET status = ?, bot_note = ?, acked_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, note_str, now_str, now_str, action_id),
        )

        # On applied for a reply action with an item and keep_open False:
        # set item status to 'done' and meta_json["handled_by_action"] = action_id
        if status == "applied" and action_row["type"] == "reply" and action_row["item_id"] and not keep_open:
            item_id = action_row["item_id"]
            it = conn.execute("SELECT meta_json FROM items WHERE id = ?", (item_id,)).fetchone()
            try:
                meta = json.loads(it["meta_json"] or "{}") if it else {}
            except Exception:
                meta = {}
            meta["handled_by_action"] = action_id
            conn.execute(
                "UPDATE items SET status = 'done', meta_json = ? WHERE id = ?",
                (json.dumps(meta), item_id),
            )

    updated = conn.execute("SELECT * FROM actions WHERE id = ?", (action_id,)).fetchone()
    res = dict(updated)
    res["payload"] = json.loads(res.get("payload_json") or "{}")
    return res


def _format_action_entry(conn: sqlite3.Connection, r: sqlite3.Row, base_url: str) -> dict[str, Any]:
    action_id = r["id"]
    item_id = r["item_id"]
    item_dict = None
    history_entries: list[dict[str, Any]] = []

    if item_id:
        item_row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        if item_row:
            rep_row = conn.execute(
                """
                SELECT ri.display_id, r.report_date
                FROM report_items ri
                JOIN reports r ON r.id = ri.report_id
                WHERE ri.item_id = ?
                ORDER BY r.report_date DESC, r.id DESC, ri.position ASC
                LIMIT 1
                """,
                (item_id,),
            ).fetchone()
            latest_display_id = rep_row["display_id"] if rep_row else None
            latest_report_date = rep_row["report_date"] if rep_row else None

            try:
                links = json.loads(item_row["links_json"] or "[]")
            except Exception:
                links = []

            bot_cli = bot_cli_name(item_row["bot_id"]) if item_row["bot_id"] else ""

            item_dict = {
                "id": item_row["id"],
                "item_key": item_row["item_key"],
                "title": item_row["title"],
                "status": item_row["status"],
                "snooze_until": item_row["snooze_until"],
                "priority": item_row["priority"],
                "links": links,
                "display_id": latest_display_id,
                "report_date": latest_report_date,
                "url": f"{base_url}/i/{item_id}",
                "bot": bot_cli,
            }

        hist_rows = conn.execute(
            """
            SELECT id, type, payload_json, status, bot_note, created_at
            FROM actions
            WHERE item_id = ? AND id < ?
            ORDER BY id ASC
            """,
            (item_id, action_id),
        ).fetchall()
        for h in hist_rows:
            try:
                h_payload = json.loads(h["payload_json"] or "{}")
            except Exception:
                h_payload = {}
            history_entries.append({
                "id": h["id"],
                "type": h["type"],
                "payload": h_payload,
                "status": h["status"],
                "bot_note": h["bot_note"],
                "created_at": h["created_at"],
            })

    try:
        payload = json.loads(r["payload_json"] or "{}")
    except Exception:
        payload = {}

    bot_cli = bot_cli_name(r["bot_id"]) if r["bot_id"] else ""

    return {
        "id": action_id,
        "type": r["type"],
        "payload": payload,
        "status": r["status"],
        "rank": r["rank"],
        "created_at": r["created_at"],
        "author": r["author"],
        "bot": bot_cli,
        "bot_note": r["bot_note"],
        "wake_status": r["wake_status"],
        "wake_requested": r["wake_requested"],
        "seen_at": r["seen_at"],
        "acked_at": r["acked_at"],
        "item": item_dict,
        "history": history_entries,
    }


def list_actions(
    conn: sqlite3.Connection,
    status: Optional[str] = None,
    bot: Optional[Bot | str] = None,
) -> list[dict[str, Any]]:
    """List actions with JSON-ready entries, ordered by rank ASC NULLS LAST, created_at, id."""
    where_clauses: list[str] = []
    params: list[Any] = []

    if status:
        where_clauses.append("status = ?")
        params.append(status)

    if bot:
        bot_obj = bot if isinstance(bot, Bot) else find_bot(bot)
        if not bot_obj:
            return []
        where_clauses.append("bot_id = ?")
        params.append(bot_obj.id)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    query = f"""
    SELECT * FROM actions
    {where_sql}
    ORDER BY rank ASC NULLS LAST, created_at ASC, id ASC
    """
    rows = conn.execute(query, params).fetchall()
    base_url = BRIEFS_PUBLIC_URL.rstrip("/")
    return [_format_action_entry(conn, r, base_url) for r in rows]


def list_actions_for_item(conn: sqlite3.Connection, item_id: int) -> list[dict[str, Any]]:
    """Return all actions recorded for a given item, newest first."""
    rows = conn.execute(
        """
        SELECT * FROM actions
        WHERE item_id = ?
        ORDER BY id DESC
        """,
        (item_id,),
    ).fetchall()
    actions: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = json.loads(d.get("payload_json") or "{}")
        except Exception:
            d["payload"] = {}
        actions.append(d)
    return actions


def inbox(
    conn: sqlite3.Connection,
    bot: Bot | str,
    include_seen: bool = False,
    peek: bool = False,
    mark_seen: Optional[bool] = None,
) -> list[dict[str, Any]]:
    """Return that bot's actions with status in (pending, queued, submitted)
    (+ seen and processing when include_seen), ordered rank then created_at.
    Unless peek: pending -> seen, queued/submitted -> processing. Sets seen_at.
    """
    bot_obj = bot if isinstance(bot, Bot) else find_bot(bot)
    if not bot_obj:
        raise ValueError(f"Unknown bot: '{bot}'")

    if mark_seen is not None:
        is_peek = not mark_seen
    else:
        is_peek = peek

    if include_seen:
        status_filter = "status IN ('pending', 'queued', 'submitted', 'seen', 'processing')"
    else:
        status_filter = "status IN ('pending', 'queued', 'submitted')"

    query = f"""
    SELECT * FROM actions
    WHERE bot_id = ? AND {status_filter}
    ORDER BY rank ASC NULLS LAST, created_at ASC, id ASC
    """
    rows = conn.execute(query, (bot_obj.id,)).fetchall()
    base_url = BRIEFS_PUBLIC_URL.rstrip("/")

    now_str = now_et().isoformat()
    pending_to_seen_ids: list[int] = []
    submitted_to_proc_ids: list[int] = []

    results: list[dict[str, Any]] = []
    for r in rows:
        entry = _format_action_entry(conn, r, base_url)
        st = entry["status"]
        if not is_peek:
            if st == "pending":
                pending_to_seen_ids.append(entry["id"])
                entry["status"] = "seen"
                entry["seen_at"] = now_str
            elif st in ("queued", "submitted"):
                submitted_to_proc_ids.append(entry["id"])
                entry["status"] = "processing"
                entry["seen_at"] = now_str
        results.append(entry)

    if not is_peek:
        with conn:
            if pending_to_seen_ids:
                ph = ",".join("?" * len(pending_to_seen_ids))
                conn.execute(
                    f"UPDATE actions SET status = 'seen', seen_at = ?, updated_at = ? WHERE id IN ({ph})",
                    [now_str, now_str] + pending_to_seen_ids,
                )
            if submitted_to_proc_ids:
                ph = ",".join("?" * len(submitted_to_proc_ids))
                conn.execute(
                    f"UPDATE actions SET status = 'processing', seen_at = ?, updated_at = ? WHERE id IN ({ph})",
                    [now_str, now_str] + submitted_to_proc_ids,
                )

    return results


def in_progress(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """One entry per item whose LATEST reply action has status in (submitted, processing, discuss):
    item fields (id, title, display_id, bot name/slug, links), the latest reply action
    (id, text, status, bot_note, updated_at) and the item's previous reply actions.
    Ordered by importance_key.
    """
    rows = conn.execute(
        """
        SELECT id, item_id, status, payload_json, bot_note, created_at, updated_at
        FROM actions
        WHERE type = 'reply' AND item_id IS NOT NULL
        ORDER BY id ASC
        """
    ).fetchall()

    items_replies: dict[int, list[sqlite3.Row]] = {}
    for r in rows:
        items_replies.setdefault(r["item_id"], []).append(r)

    in_prog_items: list[dict[str, Any]] = []
    base_url = BRIEFS_PUBLIC_URL.rstrip("/")

    for item_id, replies in items_replies.items():
        latest = replies[-1]
        if latest["status"] in ("submitted", "processing", "discuss"):
            item_row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
            if not item_row:
                continue

            bot_obj = find_bot(item_row["bot_id"])
            bot_name = bot_obj.name if bot_obj else ""
            bot_slug = bot_obj.slug if bot_obj else ""
            bot_cli = bot_obj.cli_name if bot_obj else ""

            rep_row = conn.execute(
                """
                SELECT ri.display_id
                FROM report_items ri
                JOIN reports r ON r.id = ri.report_id
                WHERE ri.item_id = ?
                ORDER BY r.report_date DESC, r.id DESC, ri.position ASC
                LIMIT 1
                """,
                (item_id,),
            ).fetchone()
            disp_id = rep_row["display_id"] if rep_row else ""

            try:
                links = json.loads(item_row["links_json"] or "[]")
            except Exception:
                links = []

            try:
                lat_payload = json.loads(latest["payload_json"] or "{}")
            except Exception:
                lat_payload = {}

            earlier = []
            for prev in replies[:-1]:
                try:
                    p_pay = json.loads(prev["payload_json"] or "{}")
                except Exception:
                    p_pay = {}
                earlier.append({
                    "id": prev["id"],
                    "text": p_pay.get("text", ""),
                    "status": prev["status"],
                    "bot_note": prev["bot_note"],
                    "created_at": prev["created_at"],
                    "updated_at": prev["updated_at"],
                })

            item_data = {
                "id": item_id,
                "title": item_row["title"],
                "display_id": disp_id,
                "bot_name": bot_name,
                "bot_slug": bot_slug,
                "bot_cli": bot_cli,
                "links": links,
                "url": f"{base_url}/i/{item_id}",
            }

            latest_action = {
                "id": latest["id"],
                "text": lat_payload.get("text", ""),
                "status": latest["status"],
                "bot_note": latest["bot_note"],
                "updated_at": latest["updated_at"],
            }

            in_prog_items.append({
                "item": item_data,
                "latest_action": latest_action,
                "earlier_actions": earlier,
                # top level convenience for templates
                "id": item_id,
                "title": item_row["title"],
                "display_id": disp_id,
                "bot_name": bot_name,
                "bot_slug": bot_slug,
                "links": links,
            })

    in_prog_items.sort(key=lambda x: importance_key(conn, x["item"]["id"]))
    return in_prog_items


def in_progress_item_ids(conn: sqlite3.Connection) -> set[int]:
    """Return set of item IDs whose latest reply action has status in ('submitted', 'processing', 'discuss')."""
    rows = conn.execute(
        """
        SELECT id, item_id, status
        FROM actions
        WHERE type = 'reply' AND item_id IS NOT NULL
        ORDER BY id ASC
        """
    ).fetchall()
    latest_status: dict[int, str] = {}
    for r in rows:
        latest_status[r["item_id"]] = r["status"]
    return {iid for iid, st in latest_status.items() if st in ("submitted", "processing", "discuss")}


def queued_item_texts(conn: sqlite3.Connection) -> dict[int, str]:
    """Return map of item_id -> text for items whose latest reply action has status 'queued'."""
    rows = conn.execute(
        """
        SELECT id, item_id, status, payload_json
        FROM actions
        WHERE type = 'reply' AND item_id IS NOT NULL
        ORDER BY id ASC
        """
    ).fetchall()
    latest_info: dict[int, tuple[str, str]] = {}
    for r in rows:
        try:
            payload = json.loads(r["payload_json"] or "{}")
        except Exception:
            payload = {}
        latest_info[r["item_id"]] = (r["status"], payload.get("text", ""))
    return {iid: text for iid, (st, text) in latest_info.items() if st == "queued"}


def queued_count(conn: sqlite3.Connection) -> int:
    """Return count of actions with status 'queued'."""
    row = conn.execute("SELECT COUNT(*) AS c FROM actions WHERE status = 'queued'").fetchone()
    return row["c"] if row else 0
