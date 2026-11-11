from __future__ import annotations

import difflib
import hashlib
import html
import json
import re
import sqlite3
from typing import Any, Optional

from markupsafe import Markup

from briefs.actions import in_progress_item_ids, queued_item_texts
from briefs.config import Bot, find_bot, now_et, today_et
from briefs.parse import filter_safe_links, is_safe_url, clean_title, compute_item_key, extract_flags, extract_links, normalize_url, parse_brief


def normalize_title_for_diff(title: str) -> str:
    """Normalize title for fuzzy similarity matching."""
    t = title.lower()
    t = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", t)
    t = re.sub(r"\b(new|updated|resolved|still|now|done)\b", " ", t)
    t = re.sub(r"[^\w\s]", " ", t)
    return " ".join(t.split())


def upsert_report(
    conn: sqlite3.Connection,
    bot: Bot | str,
    date: str,
    kind: str,
    title: str,
    body_md: str,
    sheet_url: Optional[str] = None,
    source: str = "cli",
    source_ref: Optional[str] = None,
    items: Optional[list[dict[str, Any]]] = None,
) -> tuple[int, str, int]:
    """
    Upsert a report and its items into the database.
    Used by both the importer and CLI publish.
    Returns: (report_id, 'created' | 'updated' | 'unchanged', n_items)
    """
    # Resolve bot
    bot_obj = bot if isinstance(bot, Bot) else find_bot(bot)
    if not bot_obj:
        raise ValueError(f"Unknown bot: {bot}")
    bot_id = bot_obj.id

    # If items is None, parse from body_md
    if items is None:
        parsed = parse_brief(body_md, default_date=date)
        items = parsed["items"]
        if not title:
            title = parsed["title"]
        if not sheet_url:
            sheet_url = parsed["sheet_url"]

    # Compute content hash
    if sheet_url and not is_safe_url(sheet_url):
        sheet_url = None
    content_raw = f"{title}\n{body_md}\n{sheet_url or ''}"
    content_hash = hashlib.sha256(content_raw.encode("utf-8")).hexdigest()
    now_str = now_et().isoformat()

    # Check if report already exists
    existing = conn.execute(
        "SELECT id, content_hash, source FROM reports WHERE bot_id = ? AND report_date = ? AND kind = ?",
        (bot_id, date, kind),
    ).fetchone()

    # A report the bot published itself (brief publish, source 'cli') is authoritative: the file
    # importer (nightly safety net) must never overwrite it with a re-parse that lacks stable keys.
    if existing is not None and existing["source"] == "cli" and source != "cli":
        n = conn.execute("SELECT COUNT(*) FROM report_items WHERE report_id = ?", (existing["id"],)).fetchone()[0]
        return existing["id"], "unchanged", n

    report_status = "unchanged"
    if existing is None:
        cur = conn.execute(
            """
            INSERT INTO reports (bot_id, kind, report_date, title, body_md, sheet_url, source, source_ref, content_hash, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (bot_id, kind, date, title, body_md, sheet_url, source, source_ref, content_hash, now_str, now_str),
        )
        report_id = cur.lastrowid
        report_status = "created"
    else:
        report_id = existing["id"]
        if existing["content_hash"] != content_hash:
            conn.execute(
                """
                UPDATE reports SET
                    title = ?,
                    body_md = ?,
                    sheet_url = ?,
                    source = ?,
                    source_ref = ?,
                    content_hash = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (title, body_md, sheet_url, source, source_ref, content_hash, now_str, report_id),
            )
            report_status = "updated"

    # Delete existing report_items for this report
    conn.execute("DELETE FROM report_items WHERE report_id = ?", (report_id,))

    # Candidate items for carry-forward matching (within last 10 days)
    recent_items = conn.execute(
        """
        SELECT id, item_key, title, status, meta_json
        FROM items
        WHERE bot_id = ? AND date(last_seen) >= date(?, '-10 days')
        """,
        (bot_id, date),
    ).fetchall()

    for it in items:
        display_id = str(it.get("display_id", ""))
        parent_display_id = it.get("parent_display_id")
        position = it.get("position", 0)
        section = it.get("section")
        item_text = it.get("text_md") or it.get("body_md") or ""
        it_title = it.get("title") or clean_title(item_text)
        it_links = filter_safe_links(it.get("links", []))
        it_flags = it.get("flags", set())
        if isinstance(it_flags, str):
            it_flags = {f.strip() for f in it_flags.split(",") if f.strip()}

        item_key = it.get("item_key") or compute_item_key(item_text, it_title)
        priority = it.get("priority")
        due_at = it.get("due_at")

        # 1. Check if item already exists by exact bot_id & item_key
        existing_item = conn.execute(
            "SELECT * FROM items WHERE bot_id = ? AND item_key = ?",
            (bot_id, item_key),
        ).fetchone()

        # 2. Carry-forward matching for 'h:' keys if not found
        matched_alias = None
        if existing_item is None and item_key.startswith("h:"):
            norm_new = normalize_title_for_diff(it_title)
            best_cand = None
            best_score = 0.0

            for cand in recent_items:
                norm_cand = normalize_title_for_diff(cand["title"])
                ratio = difflib.SequenceMatcher(None, norm_new, norm_cand).ratio()
                if ratio > best_score:
                    best_score = ratio
                    best_cand = cand

            if best_cand and best_score >= 0.72:
                existing_item = conn.execute(
                    "SELECT * FROM items WHERE id = ?", (best_cand["id"],)
                ).fetchone()
                if existing_item:
                    matched_alias = item_key

        # 3. Insert or update item
        if existing_item is not None:
            item_id = existing_item["id"]
            # Compute new bounds
            first_seen = min(existing_item["first_seen"] or date, date)
            last_seen = max(existing_item["last_seen"] or date, date)

            # Deduplicate & merge links
            try:
                curr_links = json.loads(existing_item["links_json"] or "[]")
            except Exception:
                curr_links = []
            seen_urls = {l.get("url") for l in curr_links}
            for l in it_links:
                if l.get("url") not in seen_urls:
                    curr_links.append(l)
                    seen_urls.add(l.get("url"))

            # Status and meta handling
            curr_status = existing_item["status"]
            try:
                meta = json.loads(existing_item["meta_json"] or "{}")
            except Exception:
                meta = {}

            if matched_alias:
                aliases = meta.setdefault("aliases", [])
                if matched_alias not in aliases:
                    aliases.append(matched_alias)

            if curr_status in ("done", "dismissed"):
                new_status = curr_status
            elif "resolved" in it_flags:
                new_status = "resolved"
            elif curr_status == "resolved":
                # Reopen to open if re-mentioned without resolved flag
                new_status = "open"
                meta["reopened_at"] = date
            elif curr_status == "snoozed":
                new_status = curr_status
            else:
                new_status = it.get("status") or curr_status

            new_priority = priority if priority is not None else existing_item["priority"]
            new_due = due_at if due_at is not None else existing_item["due_at"]

            conn.execute(
                """
                UPDATE items SET
                    title = ?,
                    status = ?,
                    priority = ?,
                    due_at = ?,
                    first_seen = ?,
                    last_seen = ?,
                    links_json = ?,
                    meta_json = ?
                WHERE id = ?
                """,
                (
                    it_title,
                    new_status,
                    new_priority,
                    new_due,
                    first_seen,
                    last_seen,
                    json.dumps(curr_links),
                    json.dumps(meta),
                    item_id,
                ),
            )
        else:
            # Create new item
            status = "resolved" if "resolved" in it_flags else it.get("status", "open")
            meta = {"aliases": [item_key]}
            cur = conn.execute(
                """
                INSERT INTO items (bot_id, item_key, title, status, priority, area, due_at, first_seen, last_seen, links_json, meta_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bot_id,
                    item_key,
                    it_title,
                    status,
                    priority,
                    bot_obj.area_default,
                    due_at,
                    date,
                    date,
                    json.dumps(it_links),
                    json.dumps(meta),
                ),
            )
            item_id = cur.lastrowid

        # Keep items_fts in sync
        conn.execute("DELETE FROM items_fts WHERE item_id = ?", (item_id,))
        conn.execute(
            "INSERT INTO items_fts (title, body, item_id) VALUES (?, ?, ?)",
            (it_title, item_text, item_id),
        )

        # Insert report_item
        flags_str = ",".join(sorted(it_flags)) if isinstance(it_flags, set) else str(it_flags or "")
        conn.execute(
            """
            INSERT INTO report_items (report_id, item_id, display_id, parent_display_id, position, section, body_md, flags)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                item_id,
                display_id,
                parent_display_id,
                position,
                section,
                item_text,
                flags_str,
            ),
        )

    # Stale marking for open items of this bot
    _update_stale_for_bot(conn, bot_id)

    conn.commit()
    return report_id, report_status, len(items)


def _update_stale_for_bot(conn: sqlite3.Connection, bot_id: str) -> None:
    """Mark open items as stale if not present in the bot's latest report."""
    latest_row = conn.execute(
        "SELECT MAX(report_date) AS max_date FROM reports WHERE bot_id = ?",
        (bot_id,),
    ).fetchone()

    if not latest_row or not latest_row["max_date"]:
        return

    latest_date = latest_row["max_date"]

    # Items in the latest reports of this bot
    rows = conn.execute(
        """
        SELECT DISTINCT ri.item_id
        FROM report_items ri
        JOIN reports r ON r.id = ri.report_id
        WHERE r.bot_id = ? AND r.report_date = ?
        """,
        (bot_id, latest_date),
    ).fetchall()
    latest_item_ids = {r["item_id"] for r in rows}

    # All open items for this bot
    open_items = conn.execute(
        "SELECT id, meta_json FROM items WHERE bot_id = ? AND status = 'open'",
        (bot_id,),
    ).fetchall()

    for row in open_items:
        try:
            meta = json.loads(row["meta_json"] or "{}")
        except Exception:
            meta = {}

        is_in_latest = row["id"] in latest_item_ids
        currently_stale = meta.get("stale", False)

        if is_in_latest and currently_stale:
            meta.pop("stale", None)
            conn.execute("UPDATE items SET meta_json = ? WHERE id = ?", (json.dumps(meta), row["id"]))
        elif not is_in_latest and not currently_stale:
            meta["stale"] = True
            conn.execute("UPDATE items SET meta_json = ? WHERE id = ?", (json.dumps(meta), row["id"]))


def distinct_areas(conn: sqlite3.Connection) -> list[str]:
    """Return distinct sorted non-null non-empty area values from items."""
    rows = conn.execute(
        "SELECT DISTINCT area FROM items WHERE area IS NOT NULL AND area != '' ORDER BY area ASC"
    ).fetchall()
    return [r["area"] for r in rows]


def items_open(
    conn: sqlite3.Connection,
    bot: Optional[str] = None,
    include_all: bool = False,
    area: Optional[str] = None,
    new_only: bool = False,
    has_due: bool = False,
    status: str = "open",
) -> list[dict[str, Any]]:
    """Query items, optionally filtered."""
    params: list[Any] = []
    where_clauses: list[str] = []

    in_prog_ids = in_progress_item_ids(conn)
    queued_texts = queued_item_texts(conn)

    eff_status = "all" if include_all else status

    if eff_status == "open":
        where_clauses.append("i.status NOT IN ('done', 'dismissed', 'resolved')")
        where_clauses.append("(i.snooze_until IS NULL OR i.snooze_until <= ?)")
        params.append(today_et())
        if in_prog_ids:
            ph = ",".join(str(x) for x in in_prog_ids)
            where_clauses.append(f"i.id NOT IN ({ph})")
    elif eff_status == "snoozed":
        where_clauses.append("i.snooze_until > ?")
        where_clauses.append("i.status NOT IN ('done', 'dismissed', 'resolved')")
        params.append(today_et())
        if in_prog_ids:
            ph = ",".join(str(x) for x in in_prog_ids)
            where_clauses.append(f"i.id NOT IN ({ph})")
    elif eff_status == "closed":
        where_clauses.append("i.status IN ('done', 'dismissed', 'resolved')")
    # eff_status == 'all': no status filter

    if bot:
        bot_obj = find_bot(bot)
        if bot_obj:
            where_clauses.append("i.bot_id = ?")
            params.append(bot_obj.id)
        else:
            return []

    if area:
        where_clauses.append("i.area = ?")
        params.append(area)

    if has_due:
        where_clauses.append("i.due_at IS NOT NULL AND i.due_at != ''")

    where_str = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    # Fetch items with their latest report_item display_id and flags
    query = f"""
    SELECT
        i.*,
        b.slug AS bot_slug,
        b.name AS bot_name,
        ri.display_id,
        ri.parent_display_id,
        ri.flags,
        ri.body_md
    FROM items i
    JOIN bots b ON b.id = i.bot_id
    LEFT JOIN report_items ri ON ri.id = (
        SELECT ri2.id
        FROM report_items ri2
        JOIN reports r2 ON r2.id = ri2.report_id
        WHERE ri2.item_id = i.id
        ORDER BY r2.report_date DESC, ri2.position ASC
        LIMIT 1
    )
    {where_str}
    ORDER BY b.sort ASC, i.priority ASC NULLS LAST, i.id ASC
    """

    rows = conn.execute(query, params).fetchall()
    result: list[dict[str, Any]] = []

    for r in rows:
        d = dict(r)
        try:
            d["meta"] = json.loads(d.get("meta_json") or "{}")
        except Exception:
            d["meta"] = {}
        try:
            d["links"] = json.loads(d.get("links_json") or "[]")
        except Exception:
            d["links"] = []

        flags_list = [f.strip() for f in (d.get("flags") or "").split(",") if f.strip()]
        d["flags_list"] = flags_list

        if new_only and "NEW" not in flags_list:
            continue

        d["queued_text"] = queued_texts.get(d["id"])
        result.append(d)

    return result


def items_snoozed(conn: sqlite3.Connection, bot: Bot | str) -> list[dict[str, Any]]:
    """Query snoozed items for a bot where snooze_until > today_et()."""
    bot_obj = bot if isinstance(bot, Bot) else find_bot(bot)
    if not bot_obj:
        return []
    today_val = today_et()
    query = """
    SELECT
        i.*,
        b.slug AS bot_slug,
        b.name AS bot_name,
        ri.display_id,
        ri.flags
    FROM items i
    JOIN bots b ON b.id = i.bot_id
    LEFT JOIN report_items ri ON ri.id = (
        SELECT ri2.id
        FROM report_items ri2
        JOIN reports r2 ON r2.id = ri2.report_id
        WHERE ri2.item_id = i.id
        ORDER BY r2.report_date DESC, ri2.position ASC
        LIMIT 1
    )
    WHERE i.bot_id = ?
      AND i.snooze_until > ?
      AND i.status NOT IN ('done', 'dismissed', 'resolved')
    ORDER BY i.snooze_until ASC, i.id ASC
    """
    rows = conn.execute(query, (bot_obj.id, today_val)).fetchall()
    result: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        try:
            d["meta"] = json.loads(d.get("meta_json") or "{}")
        except Exception:
            d["meta"] = {}
        try:
            d["links"] = json.loads(d.get("links_json") or "[]")
        except Exception:
            d["links"] = []
        result.append(d)
    return result


def today_view(
    conn: sqlite3.Connection,
    filter_bot: Optional[str] = None,
    filter_area: Optional[str] = None,
    filter_new: bool = False,
    filter_due: bool = False,
    filter_status: str = "open",
) -> dict[str, Any]:
    """
    Construct data for the Today page:
    - Chief of Staff latest list (filtered, hidden if 0 items)
    - Per bot: latest report date + title + filtered items (bots with 0 items hidden)
    """
    today_date = today_et()
    filter_bot_obj = find_bot(filter_bot) if filter_bot else None

    in_prog_ids = in_progress_item_ids(conn)
    queued_texts = queued_item_texts(conn)

    # 1. Chief of Staff latest report
    cos_bot = find_bot("cos")
    cos_data = None
    show_cos = True
    if filter_bot_obj and cos_bot and filter_bot_obj.id != cos_bot.id:
        show_cos = False

    if show_cos and cos_bot:
        cos_report = conn.execute(
            """
            SELECT * FROM reports
            WHERE bot_id = ?
            ORDER BY report_date DESC, id DESC
            LIMIT 1
            """,
            (cos_bot.id,),
        ).fetchone()

        if cos_report:
            where_clauses = ["ri.report_id = ?"]
            params: list[Any] = [cos_report["id"]]

            if filter_status == "open":
                where_clauses.append("i.status NOT IN ('done', 'dismissed', 'resolved')")
                where_clauses.append("(i.snooze_until IS NULL OR i.snooze_until <= ?)")
                params.append(today_date)
                if in_prog_ids:
                    ph = ",".join(str(x) for x in in_prog_ids)
                    where_clauses.append(f"i.id NOT IN ({ph})")
            elif filter_status == "snoozed":
                where_clauses.append("i.snooze_until > ?")
                where_clauses.append("i.status NOT IN ('done', 'dismissed', 'resolved')")
                params.append(today_date)
                if in_prog_ids:
                    ph = ",".join(str(x) for x in in_prog_ids)
                    where_clauses.append(f"i.id NOT IN ({ph})")
            elif filter_status == "closed":
                where_clauses.append("i.status IN ('done', 'dismissed', 'resolved')")
            # 'all': no status filter

            if filter_area:
                where_clauses.append("i.area = ?")
                params.append(filter_area)

            if filter_due:
                where_clauses.append("i.due_at IS NOT NULL AND i.due_at != ''")

            query = f"""
            SELECT
                ri.*,
                i.id AS item_id,
                i.title,
                i.priority,
                i.due_at,
                i.area,
                i.links_json,
                i.meta_json,
                i.status,
                i.snooze_until,
                b.slug AS bot_slug,
                b.name AS bot_name
            FROM report_items ri
            JOIN items i ON i.id = ri.item_id
            JOIN bots b ON b.id = i.bot_id
            WHERE {' AND '.join(where_clauses)}
            ORDER BY ri.position ASC
            """
            cos_items_rows = conn.execute(query, params).fetchall()

            cos_items = []
            for r in cos_items_rows:
                cd = dict(r)
                cd["id"] = cd["item_id"]
                try:
                    cd["links"] = json.loads(cd.get("links_json") or "[]")
                except Exception:
                    cd["links"] = []
                try:
                    cd["meta"] = json.loads(cd.get("meta_json") or "{}")
                except Exception:
                    cd["meta"] = {}

                flags_list = [f.strip() for f in (cd.get("flags") or "").split(",") if f.strip()]
                cd["flags_list"] = flags_list

                if filter_new and "NEW" not in flags_list:
                    continue

                cd["queued_text"] = queued_texts.get(cd["item_id"])
                cos_items.append(cd)

            if cos_items:
                cos_data = {
                    "report": dict(cos_report),
                    "items": cos_items,
                    "as_of": cos_report["report_date"],
                }

    # 2. Per-bot latest reports and items
    bot_cards = []
    all_bots = conn.execute("SELECT * FROM bots WHERE slug != 'cos' ORDER BY sort ASC").fetchall()

    for b in all_bots:
        if filter_bot_obj and b["id"] != filter_bot_obj.id:
            continue

        latest_rep = conn.execute(
            """
            SELECT * FROM reports
            WHERE bot_id = ?
            ORDER BY report_date DESC, id DESC
            LIMIT 1
            """,
            (b["id"],),
        ).fetchone()

        items = items_open(
            conn,
            bot=b["slug"],
            status=filter_status,
            area=filter_area,
            new_only=filter_new,
            has_due=filter_due,
        )

        if not items:
            continue

        bot_cards.append({
            "bot": dict(b),
            "report": dict(latest_rep) if latest_rep else None,
            "items": items,
            "is_today": latest_rep and latest_rep["report_date"] == today_date,
        })

    return {
        "today_date": today_date,
        "cos": cos_data,
        "bot_cards": bot_cards,
    }


def report_history(conn: sqlite3.Connection, bot: Optional[str] = None) -> list[dict[str, Any]]:
    """List reports, optionally filtered by bot, newest first."""
    params: list[Any] = []
    where_str = ""
    if bot:
        bot_obj = find_bot(bot)
        if bot_obj:
            where_str = "WHERE r.bot_id = ?"
            params.append(bot_obj.id)
        else:
            return []

    rows = conn.execute(
        f"""
        SELECT r.*, b.slug AS bot_slug, b.name AS bot_name,
               COUNT(ri.id) AS item_count
        FROM reports r
        JOIN bots b ON b.id = r.bot_id
        LEFT JOIN report_items ri ON ri.report_id = r.id
        {where_str}
        GROUP BY r.id
        ORDER BY r.report_date DESC, r.id DESC
        """,
        params,
    ).fetchall()

    return [dict(r) for r in rows]


def item_timeline(conn: sqlite3.Connection, item_id: int) -> Optional[dict[str, Any]]:
    """Get item details and its appearance timeline across reports."""
    item_row = conn.execute(
        """
        SELECT i.*, b.slug AS bot_slug, b.name AS bot_name
        FROM items i
        JOIN bots b ON b.id = i.bot_id
        WHERE i.id = ?
        """,
        (item_id,),
    ).fetchone()

    if not item_row:
        return None

    item = dict(item_row)
    try:
        item["links"] = json.loads(item.get("links_json") or "[]")
    except Exception:
        item["links"] = []
    try:
        item["meta"] = json.loads(item.get("meta_json") or "{}")
    except Exception:
        item["meta"] = {}

    # Appearances across reports
    appearances_rows = conn.execute(
        """
        SELECT
            ri.display_id,
            ri.parent_display_id,
            ri.flags,
            ri.body_md,
            r.id AS report_id,
            r.report_date,
            r.kind,
            r.title AS report_title,
            b.slug AS bot_slug,
            b.name AS bot_name
        FROM report_items ri
        JOIN reports r ON r.id = ri.report_id
        JOIN bots b ON b.id = r.bot_id
        WHERE ri.item_id = ?
        ORDER BY r.report_date DESC, ri.position ASC
        """,
        (item_id,),
    ).fetchall()

    timeline = []
    for app in appearances_rows:
        ad = dict(app)
        ad["flags_list"] = [f.strip() for f in (ad.get("flags") or "").split(",") if f.strip()]
        timeline.append(ad)

    item["timeline"] = timeline
    return item


def get_report(conn: sqlite3.Connection, report_id: int) -> Optional[dict[str, Any]]:
    """Fetch a report by ID along with its items and prev/next links."""
    rep_row = conn.execute(
        """
        SELECT r.*, b.slug AS bot_slug, b.name AS bot_name
        FROM reports r
        JOIN bots b ON b.id = r.bot_id
        WHERE r.id = ?
        """,
        (report_id,),
    ).fetchone()

    if not rep_row:
        return None

    rep = dict(rep_row)

    # Previous and next report of same bot
    prev_rep = conn.execute(
        """
        SELECT id, report_date, title FROM reports
        WHERE bot_id = ? AND (report_date < ? OR (report_date = ? AND id < ?))
        ORDER BY report_date DESC, id DESC
        LIMIT 1
        """,
        (rep["bot_id"], rep["report_date"], rep["report_date"], rep["id"]),
    ).fetchone()

    next_rep = conn.execute(
        """
        SELECT id, report_date, title FROM reports
        WHERE bot_id = ? AND (report_date > ? OR (report_date = ? AND id > ?))
        ORDER BY report_date ASC, id ASC
        LIMIT 1
        """,
        (rep["bot_id"], rep["report_date"], rep["report_date"], rep["id"]),
    ).fetchone()

    rep["prev"] = dict(prev_rep) if prev_rep else None
    rep["next"] = dict(next_rep) if next_rep else None

    # Items in this report
    item_rows = conn.execute(
        """
        SELECT
            ri.id AS report_item_id,
            ri.display_id,
            ri.parent_display_id,
            ri.position,
            ri.section,
            ri.body_md,
            ri.flags,
            i.id AS item_id,
            i.title AS item_title,
            i.status,
            i.priority,
            i.due_at,
            i.links_json
        FROM report_items ri
        JOIN items i ON i.id = ri.item_id
        WHERE ri.report_id = ?
        ORDER BY ri.position ASC
        """,
        (report_id,),
    ).fetchall()

    items = []
    for r in item_rows:
        d = dict(r)
        d["flags_list"] = [f.strip() for f in (d.get("flags") or "").split(",") if f.strip()]
        try:
            d["links"] = json.loads(d.get("links_json") or "[]")
        except Exception:
            d["links"] = []
        items.append(d)

    rep["items"] = items
    return rep


def get_item(conn: sqlite3.Connection, item_id: int) -> Optional[dict[str, Any]]:
    """Fetch an item by ID."""
    row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["links"] = json.loads(d.get("links_json") or "[]")
    except Exception:
        d["links"] = []
    try:
        d["meta"] = json.loads(d.get("meta_json") or "{}")
    except Exception:
        d["meta"] = {}
    return d


_SNIP_OPEN = "\x02"
_SNIP_CLOSE = "\x03"


def _safe_snippet(s: Any) -> Markup:
    """HTML-escape an FTS5 snippet, then turn the control-char markers into <mark> (H-1)."""
    if s is None:
        return Markup("")
    esc = html.escape(str(s), quote=True)
    return Markup(esc.replace(_SNIP_OPEN, "<mark>").replace(_SNIP_CLOSE, "</mark>"))


def _escape_snippets(d: dict[str, Any]) -> dict[str, Any]:
    for k in ("title_snippet", "body_snippet"):
        if k in d:
            d[k] = _safe_snippet(d[k])
    return d


def search(conn: sqlite3.Connection, query_str: str) -> dict[str, list[dict[str, Any]]]:
    """
    Full-text search over reports and items with snippets.
    Returns: {'reports': [...], 'items': [...]}
    """
    q = query_str.strip()
    if not q:
        return {"reports": [], "items": []}

    # Sanitize search term for FTS5
    # Extract alphanumeric words and build prefix matching query
    words = re.findall(r"\w+", q)
    if not words:
        return {"reports": [], "items": []}

    fts_query = " ".join([f'"{w}"*' for w in words])

    # Search reports
    reports: list[dict[str, Any]] = []
    try:
        rep_rows = conn.execute(
            """
            SELECT
                r.id,
                r.bot_id,
                b.slug AS bot_slug,
                b.name AS bot_name,
                r.report_date,
                r.kind,
                r.title,
                snippet(reports_fts, 0, char(2), char(3), '...', 15) AS title_snippet,
                snippet(reports_fts, 1, char(2), char(3), '...', 25) AS body_snippet
            FROM reports_fts
            JOIN reports r ON r.id = reports_fts.rowid
            JOIN bots b ON b.id = r.bot_id
            WHERE reports_fts MATCH ?
            ORDER BY bm25(reports_fts)
            LIMIT 25
            """,
            (fts_query,),
        ).fetchall()
        reports = [_escape_snippets(dict(r)) for r in rep_rows]
    except Exception:
        pass

    # Search items
    items: list[dict[str, Any]] = []
    try:
        item_rows = conn.execute(
            """
            SELECT
                i.id,
                i.bot_id,
                b.slug AS bot_slug,
                b.name AS bot_name,
                i.title,
                i.status,
                i.first_seen,
                i.last_seen,
                snippet(items_fts, 0, char(2), char(3), '...', 15) AS title_snippet,
                snippet(items_fts, 1, char(2), char(3), '...', 25) AS body_snippet
            FROM items_fts
            JOIN items i ON i.id = items_fts.item_id
            JOIN bots b ON b.id = i.bot_id
            WHERE items_fts MATCH ?
            ORDER BY bm25(items_fts)
            LIMIT 25
            """,
            (fts_query,),
        ).fetchall()
        items = [_escape_snippets(dict(r)) for r in item_rows]
    except Exception:
        pass

    return {"reports": reports, "items": items}
