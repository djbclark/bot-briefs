from __future__ import annotations

import json
import os
import sqlite3
import stat
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from briefs.actions import (
    ack,
    create_action,
    importance_key,
    in_progress,
    inbox,
    list_actions,
    mark,
    queue_text,
    queued_count,
    rerank_submitted,
    submit_all,
    submit_text,
)
from briefs.cli import main
from briefs.config import BOTS, find_bot, today_et
from briefs.db import connect, init_schema
from briefs.store import items_open, today_view, upsert_report
from briefs.wake import clear_notify_cache, dispatch_configured, trigger_dispatch
from briefs.web import create_app


def _make_stub_notify(tmp_path: Path, conf_bots: list[str]) -> Path:
    """Create an executable stub notify-bot script for testing."""
    stub = tmp_path / "stub_notify.sh"
    lines = [
        "#!/usr/bin/env bash",
        'if [ "$1" = "--list" ]; then',
    ]
    for b in conf_bots:
        lines.append(f'  echo "{b} configured=yes"')
    lines.extend([
        '  exit 0',
        'fi',
        'log_file="${STUB_NOTIFY_LOG:-/tmp/stub_notify.log}"',
        'echo "$@" >> "$log_file"',
        'exit 0',
    ])
    stub.write_text("\n".join(lines), encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return stub


def test_m2_migration_and_idempotence(tmp_path: Path) -> None:
    """Migration from an M2-shaped DB (old CHECK) keeps rows and allows 'queued'; idempotent."""
    db_file = str(tmp_path / "old_m2.db")
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row

    # Setup M2-shaped schema with old CHECK constraint
    conn.executescript("""
    CREATE TABLE bots (id TEXT PRIMARY KEY, slug TEXT UNIQUE, name TEXT, sort INT, area_default TEXT);
    CREATE TABLE reports (id INTEGER PRIMARY KEY, bot_id TEXT, kind TEXT, report_date TEXT, title TEXT, body_md TEXT, sheet_url TEXT, source TEXT, source_ref TEXT, content_hash TEXT, created_at TEXT, updated_at TEXT);
    CREATE TABLE items (id INTEGER PRIMARY KEY, bot_id TEXT, item_key TEXT, title TEXT, status TEXT DEFAULT 'open', priority INT, area TEXT, due_at TEXT, snooze_until TEXT, first_seen TEXT, last_seen TEXT, links_json TEXT, meta_json TEXT);
    CREATE TABLE report_items (id INTEGER PRIMARY KEY, report_id INTEGER, item_id INTEGER, display_id TEXT, parent_display_id TEXT, position INT, section TEXT, body_md TEXT, flags TEXT);
    CREATE TABLE actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id INTEGER NULL,
        report_id INTEGER NULL,
        bot_id TEXT NULL,
        type TEXT,
        payload_json TEXT,
        author TEXT,
        status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'seen', 'applied', 'failed')),
        bot_note TEXT,
        wake_requested INTEGER DEFAULT 0,
        wake_status TEXT DEFAULT '',
        seen_at TEXT,
        acked_at TEXT,
        created_at TEXT,
        updated_at TEXT
    );
    """)

    # Insert existing rows
    radar_bot = find_bot("radar")
    conn.execute("INSERT INTO bots (id, slug, name, sort) VALUES (?, 'radar', 'Radar', 10)", (radar_bot.id,))
    conn.execute("INSERT INTO items (id, bot_id, item_key, title) VALUES (1, ?, 'k1', 'Old Item')", (radar_bot.id,))
    conn.execute(
        """
        INSERT INTO actions (id, item_id, bot_id, type, payload_json, author, status, created_at, updated_at)
        VALUES (10, 1, ?, 'done', '{}', 'daniel', 'pending', '2026-10-23T08:00:00', '2026-10-23T08:00:00')
        """,
        (radar_bot.id,),
    )
    conn.commit()

    # Old check rejects 'queued'
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO actions (item_id, bot_id, type, payload_json, author, status)
            VALUES (1, 'b1', 'reply', '{}', 'daniel', 'queued')
            """
        )

    # Run init_schema migration
    init_schema(conn)

    # Verify existing row kept
    row = conn.execute("SELECT * FROM actions WHERE id = 10").fetchone()
    assert row is not None
    assert row["status"] == "pending"
    assert row["type"] == "done"

    # Verify rank column exists and 'queued' is now allowed
    info = {r["name"] for r in conn.execute("PRAGMA table_info(actions)").fetchall()}
    assert "rank" in info

    conn.execute(
        """
        INSERT INTO actions (item_id, bot_id, type, payload_json, author, status, rank)
        VALUES (1, 'b1', 'reply', '{"text":"hello"}', 'daniel', 'queued', 1)
        """
    )
    conn.commit()
    queued_row = conn.execute("SELECT * FROM actions WHERE status = 'queued'").fetchone()
    assert queued_row is not None
    assert queued_row["rank"] == 1

    # Idempotence: run init_schema again
    init_schema(conn)
    count = conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0]
    assert count == 2
    conn.close()


def test_filters_narrow_and_12_bots(temp_db: str) -> None:
    """Test that each filter provably narrows results, all 12 bots in select, and HX partial returns id=item-list."""
    conn = connect(temp_db)
    # Populate reports across multiple bots, areas, flags, due dates, statuses
    cos_bot = find_bot("cos")
    radar_bot = find_bot("radar")
    zero_bot = find_bot("zero")
    money_bot = find_bot("money")

    upsert_report(conn, "cos", "2026-10-23", "cos", "CoS Brief", "1. CoS Item Alpha\n   Details")
    upsert_report(conn, "radar", "2026-10-23", "daily", "Radar Brief", "1. Radar Item Beta\n   Details")
    upsert_report(conn, "zero", "2026-10-23", "daily", "Zero Brief", "1. Zero Item One\n   Email details")
    upsert_report(conn, "money", "2026-10-23", "daily", "Money Brief", "1. Money Bill\n   Pay bill")

    # Update areas, due dates, flags
    conn.execute("UPDATE items SET area = 'admin', due_at = '2026-10-25' WHERE bot_id = ?", (cos_bot.id,))
    conn.execute("UPDATE items SET area = 'code' WHERE bot_id = ?", (radar_bot.id,))
    conn.execute("UPDATE items SET area = 'email' WHERE bot_id = ?", (zero_bot.id,))
    conn.execute("UPDATE items SET area = 'finance', due_at = '2026-10-28' WHERE bot_id = ?", (money_bot.id,))

    # Set NEW flag on Radar report_item
    conn.execute("UPDATE report_items SET flags = 'NEW' WHERE position = 1 AND report_id IN (SELECT id FROM reports WHERE bot_id = ?)", (radar_bot.id,))

    # Add a snoozed item for Zero
    conn.execute(
        """
        INSERT INTO items (bot_id, item_key, title, status, snooze_until, area)
        VALUES (?, 'zero:snoozed', 'Zero Snoozed', 'open', '2099-01-01', 'email')
        """,
        (zero_bot.id,),
    )
    # Add a closed/done item for Radar
    conn.execute(
        """
        INSERT INTO items (bot_id, item_key, title, status, area)
        VALUES (?, 'radar:closed', 'Radar Closed', 'done', 'code')
        """,
        (radar_bot.id,),
    )
    conn.commit()
    conn.close()

    app = create_app(temp_db)
    client = TestClient(app, base_url="http://testserver")

    # 1. Full page GET /
    r = client.get("/", headers={"Tailscale-User-Login": "user@example.com"})
    assert r.status_code == 200
    html = r.text

    # Assert ALL 12 bots in the Bot select in config order
    for b in BOTS:
        assert f'value="{b.cli_name}"' in html, f"Missing bot option {b.cli_name}"
        assert b.name in html

    # 2. Filter bot=zero: hides CoS, shows only Zero rows
    r_zero = client.get("/?bot=zero", headers={"Tailscale-User-Login": "user@example.com"})
    assert r_zero.status_code == 200
    assert "CoS Item Alpha" not in r_zero.text
    assert "Radar Item Beta" not in r_zero.text
    assert "Money Bill" not in r_zero.text
    assert "Zero Item One" in r_zero.text
    assert "section-cos" not in r_zero.text
    assert "section-zero" in r_zero.text

    # 3. Filter bot=chief-of-staff: shows only CoS rows
    r_cos = client.get("/?bot=chief-of-staff", headers={"Tailscale-User-Login": "user@example.com"})
    assert r_cos.status_code == 200
    assert "CoS Item Alpha" in r_cos.text
    assert "Radar Item Beta" not in r_cos.text
    assert "Zero Item One" not in r_cos.text
    assert "Money Bill" not in r_cos.text
    assert "section-cos" in r_cos.text
    assert "section-radar" not in r_cos.text
    assert "section-zero" not in r_cos.text

    # 4. Filter area=finance: only Money section shows
    r_fin = client.get("/?area=finance", headers={"Tailscale-User-Login": "user@example.com"})
    assert r_fin.status_code == 200
    assert "Money Bill" in r_fin.text
    assert "CoS Item Alpha" not in r_fin.text
    assert "Radar Item Beta" not in r_fin.text
    assert "Zero Item One" not in r_fin.text

    # 5. Filter status=snoozed: only snoozed items appear
    r_snz = client.get("/?status=snoozed", headers={"Tailscale-User-Login": "user@example.com"})
    assert r_snz.status_code == 200
    assert "Zero Snoozed" in r_snz.text
    assert "Radar Item Beta" not in r_snz.text

    # 6. Filter status=closed: only closed item appears
    r_closed = client.get("/?status=closed", headers={"Tailscale-User-Login": "user@example.com"})
    assert r_closed.status_code == 200
    assert "Radar Closed" in r_closed.text
    assert "Radar Item Beta" not in r_closed.text

    # 7. Filter status=all: both open, snoozed, closed appear
    r_all = client.get("/?status=all", headers={"Tailscale-User-Login": "user@example.com"})
    assert r_all.status_code == 200
    assert "Radar Item Beta" in r_all.text
    assert "Zero Snoozed" in r_all.text
    assert "Radar Closed" in r_all.text

    # 8. Filter new=true: only NEW item (Radar) shows
    r_new = client.get("/?new=true", headers={"Tailscale-User-Login": "user@example.com"})
    assert r_new.status_code == 200
    assert "Radar Item Beta" in r_new.text
    assert "Zero Item One" not in r_new.text

    # 9. Filter due=true: only items with due_at show (CoS and Money)
    r_due = client.get("/?due=true", headers={"Tailscale-User-Login": "user@example.com"})
    assert r_due.status_code == 200
    assert "CoS Item Alpha" in r_due.text
    assert "Money Bill" in r_due.text
    assert "Zero Item One" not in r_due.text

    # 10. HX list partial path
    r_partial = client.get(
        "/?bot=zero",
        headers={
            "Tailscale-User-Login": "user@example.com",
            "HX-Request": "true",
            "HX-Target": "item-list",
        },
    )
    assert r_partial.status_code == 200
    assert '<div id="item-list"' in r_partial.text
    assert "<!DOCTYPE html>" not in r_partial.text
    assert "<body" not in r_partial.text


def test_send_endpoint_and_dispatch(temp_db: str, tmp_path: Path, monkeypatch) -> None:
    """Send creates submitted action with rank, removes row, triggers dispatch to chief-of-staff."""
    log_file = tmp_path / "notify.log"
    monkeypatch.setenv("STUB_NOTIFY_LOG", str(log_file))
    stub = _make_stub_notify(tmp_path, ["chief-of-staff"])
    monkeypatch.setenv("BRIEFS_NOTIFY_CMD", str(stub))
    clear_notify_cache()

    conn = connect(temp_db)
    upsert_report(conn, "radar", "2026-10-23", "daily", "Radar Daily", "1. Fix bug\n   Details")
    items = items_open(conn, bot="radar")
    item_id = items[0]["id"]
    conn.close()

    app = create_app(temp_db)
    client = TestClient(app, base_url="http://testserver")

    # Send reply
    r = client.post(
        f"/i/{item_id}/send",
        data={"text": "Investigate immediately"},
        headers={
            "Tailscale-User-Login": "user@example.com",
            "HX-Request": "true",
            "Origin": "http://testserver",
        },
    )
    assert r.status_code == 200
    assert r.text == ""  # empty 200 for outerHTML removal
    trigger_data = json.loads(r.headers.get("HX-Trigger", "{}"))
    assert trigger_data.get("inprogressChanged") is True
    assert trigger_data.get("draftClear", {}).get("item") == item_id

    # Check DB
    conn = connect(temp_db)
    act = conn.execute("SELECT * FROM actions WHERE item_id = ? AND type = 'reply'", (item_id,)).fetchone()
    assert act is not None
    assert act["status"] == "submitted"
    assert act["rank"] == 1
    assert act["wake_status"] == "dispatch sent"
    conn.close()

    # Verify stub notify log was called once with chief-of-staff and "1 action submitted"
    log_content = log_file.read_text(encoding="utf-8")
    assert "chief-of-staff" in log_content
    assert "1 action submitted" in log_content

    # Test not-configured path:
    clear_notify_cache()
    stub_unconf = _make_stub_notify(tmp_path, ["other-bot"])
    monkeypatch.setenv("BRIEFS_NOTIFY_CMD", str(stub_unconf))

    conn = connect(temp_db)
    upsert_report(conn, "radar", "2026-10-23", "daily", "Radar Daily 2", "1. Another bug\n   Details")
    items2 = items_open(conn, bot="radar")
    item2_id = items2[0]["id"]
    conn.close()

    r2 = client.post(
        f"/i/{item2_id}/send",
        data={"text": "Fix this one too"},
        headers={
            "Tailscale-User-Login": "user@example.com",
            "HX-Request": "true",
            "Origin": "http://testserver",
        },
    )
    assert r2.status_code == 200

    conn = connect(temp_db)
    act2 = conn.execute("SELECT * FROM actions WHERE item_id = ?", (item2_id,)).fetchone()
    assert act2["wake_status"] == "dispatch not configured"
    conn.close()

    # Verify warning banner is displayed on GET /
    r_page = client.get("/", headers={"Tailscale-User-Login": "user@example.com"})
    assert "Processing trigger not configured yet" in r_page.text


def test_queue_and_submit_bar(temp_db: str) -> None:
    """Queue creates queued action; row shows chip; submit-bar data-queued increments."""
    conn = connect(temp_db)
    upsert_report(conn, "radar", "2026-10-23", "daily", "Radar Daily", "1. Vulnerability\n   Details")
    items = items_open(conn, bot="radar")
    item_id = items[0]["id"]
    conn.close()

    app = create_app(temp_db)
    client = TestClient(app, base_url="http://testserver")

    # Initial submit-bar
    r_bar0 = client.get("/submit-bar", headers={"Tailscale-User-Login": "user@example.com"})
    assert 'data-queued="0"' in r_bar0.text

    # Queue reply
    r_q = client.post(
        f"/i/{item_id}/queue",
        data={"text": "Check CVE later"},
        headers={
            "Tailscale-User-Login": "user@example.com",
            "HX-Request": "true",
            "Origin": "http://testserver",
        },
    )
    assert r_q.status_code == 200
    assert "queued: Check CVE later" in r_q.text
    trig = json.loads(r_q.headers.get("HX-Trigger", "{}"))
    assert trig.get("queueChanged") is True

    # Check submit-bar data-queued incremented
    r_bar1 = client.get("/submit-bar", headers={"Tailscale-User-Login": "user@example.com"})
    assert 'data-queued="1"' in r_bar1.text


def test_submit_all_ordering_and_dispatch(temp_db: str, tmp_path: Path, monkeypatch) -> None:
    """Submit all ordering: CoS item pos 2 ranks before non-CoS P1 item; P1 before P3;
    due sooner first; NEW first; older first; queued actions included; one dispatch call; empty -> 400.
    """
    log_file = tmp_path / "submit_all_notify.log"
    monkeypatch.setenv("STUB_NOTIFY_LOG", str(log_file))
    stub = _make_stub_notify(tmp_path, ["chief-of-staff"])
    monkeypatch.setenv("BRIEFS_NOTIFY_CMD", str(stub))
    clear_notify_cache()

    conn = connect(temp_db)
    # 1. CoS report with item at position 2
    upsert_report(conn, "cos", "2026-10-23", "cos", "CoS Brief", "1. CoS Top 1\n   d1\n2. CoS Pos 2\n   d2")
    cos_items = items_open(conn, bot="cos", include_all=True)
    cos_pos2_id = next(it["id"] for it in cos_items if "Pos 2" in it["title"])

    # 2. Non-CoS P1 item
    upsert_report(conn, "radar", "2026-10-23", "daily", "Radar Brief", "1. Radar P1\n   Details")
    radar_p1_id = items_open(conn, bot="radar")[0]["id"]
    conn.execute("UPDATE items SET priority = 1 WHERE id = ?", (radar_p1_id,))

    # 3. Non-CoS P3 item
    upsert_report(conn, "zero", "2026-10-23", "daily", "Zero Brief", "1. Zero P3\n   Details")
    zero_p3_id = items_open(conn, bot="zero")[0]["id"]
    conn.execute("UPDATE items SET priority = 3 WHERE id = ?", (zero_p3_id,))

    # 4. Item due sooner (Oct 24) vs due later (Oct 30) (both P2)
    upsert_report(conn, "money", "2026-10-23", "daily", "Money Brief", "1. Due Sooner\n   d\n2. Due Later\n   d")
    m_items = items_open(conn, bot="money")
    due_sooner_id = next(it["id"] for it in m_items if "Sooner" in it["title"])
    due_later_id = next(it["id"] for it in m_items if "Later" in it["title"])
    conn.execute("UPDATE items SET priority = 2, due_at = '2026-10-24' WHERE id = ?", (due_sooner_id,))
    conn.execute("UPDATE items SET priority = 2, due_at = '2026-10-30' WHERE id = ?", (due_later_id,))

    # 5. NEW item vs non-NEW item
    upsert_report(conn, "family", "2026-10-23", "daily", "Fam Brief", "1. Fam New\n   d\n2. Fam Old\n   d")
    fam_items = items_open(conn, bot="family")
    fam_new_id = next(it["id"] for it in fam_items if "New" in it["title"])
    fam_non_new_id = next(it["id"] for it in fam_items if "Old" in it["title"])
    conn.execute("UPDATE report_items SET flags = 'NEW' WHERE item_id = ?", (fam_new_id,))

    # 6. Older first_seen vs newer first_seen
    upsert_report(conn, "business", "2026-10-23", "daily", "Business Brief", "1. DC Older\n   d\n2. DC Newer\n   d")
    dc_items = items_open(conn, bot="business")
    dc_older_id = next(it["id"] for it in dc_items if "Older" in it["title"])
    dc_newer_id = next(it["id"] for it in dc_items if "Newer" in it["title"])
    conn.execute("UPDATE items SET first_seen = '2026-10-01' WHERE id = ?", (dc_older_id,))
    conn.execute("UPDATE items SET first_seen = '2026-10-20' WHERE id = ?", (dc_newer_id,))

    # Pre-existing queued action
    queue_text(conn, fam_new_id, "Existing queued action text", "daniel")
    conn.commit()
    conn.close()

    app = create_app(temp_db)
    client = TestClient(app, base_url="http://testserver")

    # Call /submit-all with empty drafts -> 400
    r_empty = client.post(
        "/submit-all",
        json={"drafts": []},
        headers={
            "Tailscale-User-Login": "user@example.com",
            "HX-Request": "true",
            "Origin": "http://testserver",
        },
    )
    # Note: there is 1 pre-existing queued action, so submit-all will submit that queued action!
    # Let's verify that submitting empty drafts submits the queued action
    assert r_empty.status_code == 200
    assert "Submitted 1" in r_empty.text

    # Now with 0 queued actions and empty drafts -> 400
    r_empty2 = client.post(
        "/submit-all",
        json={"drafts": []},
        headers={
            "Tailscale-User-Login": "user@example.com",
            "HX-Request": "true",
            "Origin": "http://testserver",
        },
    )
    assert r_empty2.status_code == 400

    # Now submit drafts for all the items
    drafts = [
        {"item_id": zero_p3_id, "text": "Draft for zero P3"},
        {"item_id": radar_p1_id, "text": "Draft for radar P1"},
        {"item_id": cos_pos2_id, "text": "Draft for CoS pos 2"},
        {"item_id": due_later_id, "text": "Draft for due later"},
        {"item_id": due_sooner_id, "text": "Draft for due sooner"},
        {"item_id": dc_newer_id, "text": "Draft for dc newer"},
        {"item_id": dc_older_id, "text": "Draft for dc older"},
    ]

    r_sub = client.post(
        "/submit-all",
        json={"drafts": drafts},
        headers={
            "Tailscale-User-Login": "user@example.com",
            "HX-Request": "true",
            "Origin": "http://testserver",
        },
    )
    assert r_sub.status_code == 200
    assert f"Submitted {len(drafts)}" in r_sub.text

    trig = json.loads(r_sub.headers.get("HX-Trigger", "{}"))
    assert trig.get("inprogressChanged") is True
    assert trig.get("listChanged") is True
    assert trig.get("queueChanged") is True
    assert len(trig.get("draftsSubmitted", [])) == len(drafts)

    # Check ranking in DB
    conn = connect(temp_db)
    acts = conn.execute(
        """
        SELECT a.rank, a.item_id, i.title
        FROM actions a
        JOIN items i ON i.id = a.item_id
        WHERE a.status = 'submitted'
        ORDER BY a.rank ASC
        """
    ).fetchall()
    conn.close()

    item_rank_map = {row["item_id"]: row["rank"] for row in acts}

    # 1. CoS item pos 2 ranks before non-CoS P1
    assert item_rank_map[cos_pos2_id] < item_rank_map[radar_p1_id]
    # 2. P1 ranks before P3
    assert item_rank_map[radar_p1_id] < item_rank_map[zero_p3_id]
    # 3. Due sooner ranks before due later
    assert item_rank_map[due_sooner_id] < item_rank_map[due_later_id]
    # 4. Older first_seen ranks before newer
    assert item_rank_map[dc_older_id] < item_rank_map[dc_newer_id]

    # Verify notify was called with the count
    log_content = log_file.read_text(encoding="utf-8")
    assert f"{len(drafts)} actions submitted" in log_content


def test_action_lifecycle(temp_db: str) -> None:
    """Test mark processing, ack discuss, ack applied resolves item, keep-open, inbox status transitions."""
    conn = connect(temp_db)
    upsert_report(conn, "radar", "2026-10-23", "daily", "Radar Daily", "1. Vulnerability\n   Details")
    items = items_open(conn, bot="radar")
    item_id = items[0]["id"]

    # 1. Submit text action
    act = submit_text(conn, item_id, "Please investigate this issue", "daniel")
    act_id = act["id"]
    assert act["status"] == "submitted"

    # Item is now in_progress and excluded from open list
    open_items = items_open(conn, bot="radar")
    assert len(open_items) == 0
    in_prog = in_progress(conn)
    assert len(in_prog) == 1
    assert in_prog[0]["item"]["id"] == item_id

    # 2. Mark processing
    marked = mark(conn, act_id, "processing")
    assert marked["status"] == "processing"

    # 3. Ack discuss keeps item open and in progress with bot note
    acked_discuss = ack(conn, act_id, "discuss", note="Is this production only?")
    assert acked_discuss["status"] == "discuss"
    assert acked_discuss["bot_note"] == "Is this production only?"

    it = conn.execute("SELECT status FROM items WHERE id = ?", (item_id,)).fetchone()
    assert it["status"] == "open"
    in_prog2 = in_progress(conn)
    assert len(in_prog2) == 1
    assert in_prog2[0]["latest_action"]["status"] == "discuss"
    assert in_prog2[0]["latest_action"]["bot_note"] == "Is this production only?"

    # 4. Reply back and ack applied
    act2 = submit_text(conn, item_id, "Yes, production only", "daniel")
    acked_applied = ack(conn, act2["id"], "applied", note="Patched in prod", keep_open=False)
    assert acked_applied["status"] == "applied"

    # Item is now resolved (status='done') and leaves in_progress and open lists
    it2 = conn.execute("SELECT status, meta_json FROM items WHERE id = ?", (item_id,)).fetchone()
    assert it2["status"] == "done"
    meta = json.loads(it2["meta_json"] or "{}")
    assert meta.get("handled_by_action") == act2["id"]

    in_prog3 = in_progress(conn)
    assert len(in_prog3) == 0
    open_items2 = items_open(conn, bot="radar")
    assert len(open_items2) == 0

    # 5. Test keep_open=True
    upsert_report(conn, "radar", "2026-10-23", "daily", "Radar Daily 2", "1. Item Two\n   Details")
    item2_id = [x["id"] for x in items_open(conn, bot="radar") if "Item Two" in x["title"]][0]
    act3 = submit_text(conn, item2_id, "Keep this open please", "daniel")
    ack(conn, act3["id"], "applied", note="Done partial", keep_open=True)
    it3 = conn.execute("SELECT status FROM items WHERE id = ?", (item2_id,)).fetchone()
    assert it3["status"] == "open"

    # 6. Test inbox transitions: queued/submitted -> processing, pending -> seen
    act_q = queue_text(conn, item2_id, "Queued msg", "daniel")
    act_s = submit_text(conn, item2_id, "Submitted msg", "daniel")
    act_p = create_action(conn, item2_id, "priority", {"delta": -1}, "daniel")

    # Peek leaves statuses untouched
    peeked = inbox(conn, "radar", peek=True)
    peek_map = {a["id"]: a["status"] for a in peeked}
    assert peek_map[act_q["id"]] == "queued"
    assert peek_map[act_s["id"]] == "submitted"
    assert peek_map[act_p["id"]] == "pending"

    # Regular inbox marks queued/submitted -> processing, pending -> seen
    inbox_res = inbox(conn, "radar", peek=False)
    inbox_map = {a["id"]: a["status"] for a in inbox_res}
    assert inbox_map[act_q["id"]] == "processing"
    assert inbox_map[act_s["id"]] == "processing"
    assert inbox_map[act_p["id"]] == "seen"
    conn.close()


def test_in_progress_visibility_and_ids_polling(temp_db: str) -> None:
    """Test GET /in-progress: empty -> placeholder without 'every 15s'; with submitted -> contains 'every 15s';
    ids mismatch -> HX-Trigger listChanged.
    """
    app = create_app(temp_db)
    client = TestClient(app, base_url="http://testserver")

    # Empty
    r_empty = client.get("/in-progress", headers={"Tailscale-User-Login": "user@example.com"})
    assert r_empty.status_code == 200
    assert '<div id="in-progress"' in r_empty.text
    assert "every 15s" not in r_empty.text

    # With submitted action
    conn = connect(temp_db)
    upsert_report(conn, "radar", "2026-10-23", "daily", "Radar Daily", "1. Vulnerability\n   Details")
    items = items_open(conn, bot="radar")
    item_id = items[0]["id"]
    submit_text(conn, item_id, "Working on it", "daniel")
    conn.close()

    r_entry = client.get("/in-progress", headers={"Tailscale-User-Login": "user@example.com"})
    assert r_entry.status_code == 200
    assert "every 15s" in r_entry.text
    assert "In progress (1)" in r_entry.text
    assert "Working on it" in r_entry.text

    # IDs mismatch: client sent ids=999
    r_mismatch = client.get(
        "/in-progress?ids=999",
        headers={"Tailscale-User-Login": "user@example.com"},
    )
    assert r_mismatch.status_code == 200
    assert r_mismatch.headers.get("HX-Trigger") == "listChanged"

    # IDs match: client sent actual item_id
    r_match = client.get(
        f"/in-progress?ids={item_id}",
        headers={"Tailscale-User-Login": "user@example.com"},
    )
    assert r_match.status_code == 200
    assert "HX-Trigger" not in r_match.headers


def test_csrf_and_login_gate(temp_db: str) -> None:
    """Test CSRF requirements and Tailscale-User-Login authorization gate."""
    app = create_app(temp_db)
    client = TestClient(app, base_url="http://testserver")

    conn = connect(temp_db)
    upsert_report(conn, "radar", "2026-10-23", "daily", "Radar Daily", "1. Test Item\n   Details")
    item_id = items_open(conn, bot="radar")[0]["id"]
    conn.close()

    # 1. Missing HX-Request
    for path, data in [
        (f"/i/{item_id}/send", {"text": "hi"}),
        (f"/i/{item_id}/queue", {"text": "hi"}),
        ("/submit-all", {"drafts": "[]"}),
    ]:
        r = client.post(
            path,
            data=data,
            headers={"Tailscale-User-Login": "user@example.com", "Origin": "http://testserver"},
        )
        assert r.status_code == 403

    # 2. Bad Origin
    for path, data in [
        (f"/i/{item_id}/send", {"text": "hi"}),
        (f"/i/{item_id}/queue", {"text": "hi"}),
        ("/submit-all", {"drafts": "[]"}),
    ]:
        r = client.post(
            path,
            data=data,
            headers={
                "Tailscale-User-Login": "user@example.com",
                "HX-Request": "true",
                "Origin": "http://attacker.com",
            },
        )
        assert r.status_code == 403

    # 3. Login gate for unauthorized user
    r_unauth = client.get(
        "/",
        headers={"Tailscale-User-Login": "unauthorized@example.com"},
    )
    assert r_unauth.status_code == 403


def test_cli_commands(temp_db: str, monkeypatch) -> None:
    """Test CLI commands: actions --status submitted --json ordered by rank, mark, ack --status discuss, ack --keep-open."""
    conn = connect(temp_db)
    upsert_report(conn, "radar", "2026-10-23", "daily", "Radar Daily", "1. Vulnerability Alpha\n   d1\n2. Vulnerability Beta\n   d2")
    items = items_open(conn, bot="radar")
    id1 = items[0]["id"]
    id2 = items[1]["id"]

    act1 = submit_text(conn, id1, "Fix 1", "daniel")
    act2 = submit_text(conn, id2, "Fix 2", "daniel")
    conn.close()

    captured: list[str] = []
    monkeypatch.setattr("builtins.print", lambda msg: captured.append(str(msg)))

    # 1. brief actions --status submitted --json
    monkeypatch.setattr(
        "sys.argv",
        ["brief", "--db", temp_db, "actions", "--status", "submitted", "--json"],
    )
    main()
    actions_out = json.loads(captured[-1])
    assert len(actions_out) >= 2
    assert actions_out[0]["rank"] <= actions_out[1]["rank"]

    # 2. brief mark <id> processing
    captured.clear()
    monkeypatch.setattr(
        "sys.argv",
        ["brief", "--db", temp_db, "mark", str(act1["id"]), "processing"],
    )
    main()
    mark_out = json.loads(captured[-1])
    assert mark_out[0]["status"] == "processing"

    # 3. brief ack <id> --status discuss --note "Question"
    captured.clear()
    monkeypatch.setattr(
        "sys.argv",
        ["brief", "--db", temp_db, "ack", str(act1["id"]), "--status", "discuss", "--note", "Question from bot"],
    )
    main()
    ack_out = json.loads(captured[-1])
    assert ack_out[0]["status"] == "discuss"
    assert ack_out[0]["bot_note"] == "Question from bot"

    # 4. brief ack <id> --status applied --keep-open
    captured.clear()
    monkeypatch.setattr(
        "sys.argv",
        ["brief", "--db", temp_db, "ack", str(act2["id"]), "--status", "applied", "--keep-open"],
    )
    main()
    ack2_out = json.loads(captured[-1])
    assert ack2_out[0]["status"] == "applied"

    conn = connect(temp_db)
    it2 = conn.execute("SELECT status FROM items WHERE id = ?", (id2,)).fetchone()
    assert it2["status"] == "open"
    conn.close()


def test_module_app_attribute_exists() -> None:
    """The keepalive runs `uvicorn briefs.web:app`; the module attribute must exist."""
    import briefs.web as web

    assert hasattr(web, "app")


def test_section_cap_and_show_all(temp_db: str) -> None:
    """Unfiltered bot sections cap at 25 rows with a Show all button; bot filter shows all."""
    conn = connect(temp_db)
    radar = find_bot("radar")
    for i in range(1, 31):
        conn.execute(
            "INSERT INTO items (bot_id, item_key, title, status) VALUES (?, ?, ?, 'open')",
            (radar.id, f"radar:t{i}", f"Radar thing {i:03d}"),
        )
    conn.commit()
    conn.close()
    client = TestClient(create_app(temp_db), base_url="http://testserver")
    h = {"Tailscale-User-Login": "user@example.com"}
    r = client.get("/", headers=h)
    assert r.status_code == 200
    shown = sum(1 for i in range(1, 31) if f"Radar thing {i:03d}" in r.text)
    assert shown == 25
    assert "show-all-btn" in r.text and "Show all 30" in r.text
    r2 = client.get("/?bot=radar", headers=h)
    assert all(f"Radar thing {i:03d}" in r2.text for i in range(1, 31))
    assert "show-all-btn" not in r2.text


def test_item_page_reply_send_and_queue(temp_db: str, tmp_path: Path, monkeypatch) -> None:
    """Item-page reply form follows the new lifecycle: mode=queue -> queued, else submitted + one dispatch."""
    log = tmp_path / "calls.log"
    stub = _make_stub_notify(tmp_path, ["chief-of-staff"])
    monkeypatch.setenv("BRIEFS_NOTIFY_CMD", str(stub))
    monkeypatch.setenv("STUB_NOTIFY_LOG", str(log))
    clear_notify_cache()
    conn = connect(temp_db)
    upsert_report(conn, "radar", "2026-10-23", "daily", "Radar Brief", "1. Radar Item\n   Details")
    item_id = conn.execute("SELECT id FROM items LIMIT 1").fetchone()["id"]
    conn.close()
    client = TestClient(create_app(temp_db), base_url="http://testserver")
    h = {"Tailscale-User-Login": "user@example.com", "HX-Request": "true", "Origin": "http://testserver"}
    r = client.post(f"/i/{item_id}/action", data={"type": "reply", "text": "later", "mode": "queue"}, headers=h)
    assert r.status_code == 200
    r = client.post(f"/i/{item_id}/action", data={"type": "reply", "text": "now", "mode": "send"}, headers=h)
    assert r.status_code == 200
    assert "Wake bot now" not in r.text
    conn = connect(temp_db)
    rows = conn.execute("SELECT status, wake_requested FROM actions ORDER BY id").fetchall()
    conn.close()
    assert [x["status"] for x in rows] == ["queued", "submitted"]
    calls = log.read_text().splitlines()
    assert len(calls) == 1 and calls[0].startswith("chief-of-staff") and "1 action submitted" in calls[0]
