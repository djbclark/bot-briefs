from __future__ import annotations

import json
import os
import sqlite3
import stat
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from briefs.actions import ack, create_action, inbox, list_actions_for_item
from briefs.cli import main, parse_sheet_rows_data
from briefs.config import BRIEFS_PUBLIC_URL, find_bot, today_et
from briefs.db import connect, init_schema
from briefs.store import items_open, items_snoozed, upsert_report
from briefs.wake import clear_notify_cache, notify_configured, wake_bot
from briefs.web import create_app


def test_schema_migration_idempotent(tmp_path: Path) -> None:
    """Test idempotent migration on an existing DB that lacks new actions columns."""
    db_file = str(tmp_path / "old_schema.db")
    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    # Create old actions table without new columns
    conn.executescript("""
    CREATE TABLE bots (id TEXT PRIMARY KEY, slug TEXT UNIQUE, name TEXT, sort INT, area_default TEXT);
    CREATE TABLE items (id INTEGER PRIMARY KEY, bot_id TEXT, item_key TEXT, title TEXT, status TEXT DEFAULT 'open', priority INT, area TEXT, due_at TEXT, snooze_until TEXT, first_seen TEXT, last_seen TEXT, links_json TEXT, meta_json TEXT);
    CREATE TABLE actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id INTEGER,
        report_id INTEGER,
        type TEXT,
        payload_json TEXT,
        author TEXT,
        status TEXT DEFAULT 'pending',
        bot_note TEXT,
        created_at TEXT,
        updated_at TEXT
    );
    """)
    conn.commit()

    # Verify old table lacks bot_id
    info = {r["name"] for r in conn.execute("PRAGMA table_info(actions)").fetchall()}
    assert "bot_id" not in info
    assert "wake_requested" not in info

    # Run init_schema
    init_schema(conn)

    # Verify new columns exist
    info2 = {r["name"] for r in conn.execute("PRAGMA table_info(actions)").fetchall()}
    for col in ("bot_id", "wake_requested", "wake_status", "seen_at", "acked_at"):
        assert col in info2, f"Missing column {col}"

    # Verify index exists
    idx = conn.execute("PRAGMA index_list(actions)").fetchall()
    assert any(r["name"] == "idx_actions_bot_status" for r in idx)

    # Re-running init_schema should be a clean no-op
    init_schema(conn)
    conn.close()


def test_bot_cli_names() -> None:
    """Verify bot cli_name mapping and find_bot acceptance."""
    expected = {
        "cos": "chief-of-staff",
        "radar": "radar",
        "zero": "zero",
        "family": "family",
        "money": "money",
        "business": "business",
        "chess": "chess",
        "pika": "pika",
        "friends": "friends",
        "te": "techno-euphora",
        "oneoffs": "one-offs",
        "coding": "coding",
    }
    for slug, cli_name in expected.items():
        bot = find_bot(slug)
        assert bot is not None, f"Bot not found for {slug}"
        assert bot.cli_name == cli_name
        # Lookup by cli_name
        bot_by_cli = find_bot(cli_name)
        assert bot_by_cli is not None
        assert bot_by_cli.slug == slug


def test_actions_local_effects(temp_db: str) -> None:
    """Test local effects of all action types in one transaction."""
    conn = connect(temp_db)
    # Create report & item
    upsert_report(conn, "radar", "2026-10-23", "daily", "Radar Daily", "1. Item Alpha\n   Details")
    items = items_open(conn, bot="radar", include_all=True)
    assert len(items) == 1
    item_id = items[0]["id"]

    # 1. done action
    act_done = create_action(conn, item_id, "done", {}, "daniel")
    assert act_done["status"] == "pending"
    assert act_done["type"] == "done"
    it = conn.execute("SELECT status FROM items WHERE id = ?", (item_id,)).fetchone()
    assert it["status"] == "done"

    # 2. reopen action
    create_action(conn, item_id, "reopen", {}, "daniel")
    it = conn.execute("SELECT status, snooze_until FROM items WHERE id = ?", (item_id,)).fetchone()
    assert it["status"] == "open"
    assert it["snooze_until"] is None

    # 3. dismiss action
    create_action(conn, item_id, "dismiss", {}, "daniel")
    it = conn.execute("SELECT status FROM items WHERE id = ?", (item_id,)).fetchone()
    assert it["status"] == "dismissed"

    # Reopen again
    create_action(conn, item_id, "reopen", {}, "daniel")

    # 4. snooze action
    with pytest.raises(ValueError, match="future"):
        create_action(conn, item_id, "snooze", {"until": today_et()}, "daniel")
    act_snooze = create_action(conn, item_id, "snooze", {"until": "2099-12-31"}, "daniel")
    it = conn.execute("SELECT snooze_until FROM items WHERE id = ?", (item_id,)).fetchone()
    assert it["snooze_until"] == "2099-12-31"

    # Reopen clears snooze
    create_action(conn, item_id, "reopen", {}, "daniel")
    it = conn.execute("SELECT snooze_until FROM items WHERE id = ?", (item_id,)).fetchone()
    assert it["snooze_until"] is None

    # 5. priority action
    # Default priority treats NULL as 3. delta -1 -> 2
    create_action(conn, item_id, "priority", {"delta": -1}, "daniel")
    it = conn.execute("SELECT priority FROM items WHERE id = ?", (item_id,)).fetchone()
    assert it["priority"] == 2

    # Clamp to 1..5: decrement 3 times
    create_action(conn, item_id, "priority", {"delta": -1}, "daniel")
    create_action(conn, item_id, "priority", {"delta": -1}, "daniel")
    it = conn.execute("SELECT priority FROM items WHERE id = ?", (item_id,)).fetchone()
    assert it["priority"] == 1

    # 6. reply action
    with pytest.raises(ValueError, match="text"):
        create_action(conn, item_id, "reply", {"text": ""}, "daniel")
    act_reply = create_action(conn, item_id, "reply", {"text": "Please fix this", "wake": True}, "daniel")
    assert act_reply["wake_requested"] == 1
    it = conn.execute("SELECT status FROM items WHERE id = ?", (item_id,)).fetchone()
    assert it["status"] == "open"

    # Verify list_actions_for_item
    history = list_actions_for_item(conn, item_id)
    assert len(history) >= 6
    conn.close()


def test_snooze_hides_then_reappears(temp_db: str, monkeypatch) -> None:
    """Test snooze hides item from open view, then reappears when date passes."""
    conn = connect(temp_db)
    upsert_report(conn, "zero", "2026-10-23", "daily", "Zero Daily", "1. Email follow up\n   Details")
    items = items_open(conn, bot="zero")
    assert len(items) == 1
    item_id = items[0]["id"]

    # Pin "today" BEFORE snoozing: create_action rejects an until date that is not after today ET,
    # so with the real clock this test broke once 2026-10-25 passed.
    monkeypatch.setattr("briefs.store.today_et", lambda: "2026-10-23")
    monkeypatch.setattr("briefs.actions.today_et", lambda: "2026-10-23")

    # Snooze until 2026-10-25; while today is 2026-10-23, open view excludes it
    create_action(conn, item_id, "snooze", {"until": "2026-10-25"}, "daniel")
    open_items = items_open(conn, bot="zero")
    assert len(open_items) == 0

    # On 2026-10-24, still hidden
    monkeypatch.setattr("briefs.store.today_et", lambda: "2026-10-24")
    assert len(items_open(conn, bot="zero")) == 0

    # On 2026-10-25 (until date), it reappears! (snooze_until <= today_et)
    monkeypatch.setattr("briefs.store.today_et", lambda: "2026-10-25")
    reappeared = items_open(conn, bot="zero")
    assert len(reappeared) == 1
    assert reappeared[0]["id"] == item_id
    conn.close()


def test_done_dismiss_hidden_from_open_view(temp_db: str) -> None:
    """Done and dismissed items must be hidden from open view."""
    conn = connect(temp_db)
    body = "1. Bank balance check\n   Review checking\n2. Review invoice\n   Due soon\n3. Audit payroll\n   Monthly audit"
    upsert_report(conn, "money", "2026-10-23", "daily", "Money Daily", body)

    items = items_open(conn, bot="money")
    assert len(items) == 3

    create_action(conn, items[0]["id"], "done", {}, "tester")
    create_action(conn, items[1]["id"], "dismiss", {}, "tester")

    open_items = items_open(conn, bot="money")
    assert len(open_items) == 1
    assert open_items[0]["id"] == items[2]["id"]

    all_items = items_open(conn, bot="money", include_all=True)
    assert len(all_items) == 3
    conn.close()


def test_republish_does_not_reopen_done_or_dismissed(temp_db: str) -> None:
    """Republishing must never reopen done or dismissed items, and must not clear snooze_until."""
    conn = connect(temp_db)
    body1 = "1. Bank Sync\n   https://github.com/bank/sync\n2. Invoice Review\n   https://github.com/inv/rev"
    upsert_report(conn, "money", "2026-10-23", "daily", "Money Oct 23", body1)

    items = items_open(conn, bot="money")
    assert len(items) == 2
    item1_id = items[0]["id"]
    item2_id = items[1]["id"]

    create_action(conn, item1_id, "done", {}, "tester")
    create_action(conn, item2_id, "dismiss", {}, "tester")
    create_action(conn, item1_id, "snooze", {"until": "2099-01-01"}, "tester")

    # Republish for next day with same items
    body2 = "1. Bank Sync\n   https://github.com/bank/sync\n2. Invoice Review\n   https://github.com/inv/rev"
    upsert_report(conn, "money", "2026-10-24", "daily", "Money Oct 24", body2)

    # Check statuses in items table
    row1 = conn.execute("SELECT status, snooze_until FROM items WHERE id = ?", (item1_id,)).fetchone()
    row2 = conn.execute("SELECT status FROM items WHERE id = ?", (item2_id,)).fetchone()

    assert row1["status"] == "done", "Done item must NOT be reopened by republish"
    assert row1["snooze_until"] == "2099-01-01", "snooze_until must NOT be cleared by republish"
    assert row2["status"] == "dismissed", "Dismissed item must NOT be reopened by republish"
    conn.close()


def test_inbox_and_ack(temp_db: str) -> None:
    """Test inbox marks seen, include_seen, and ack applied/failed."""
    conn = connect(temp_db)
    upsert_report(conn, "radar", "2026-10-23", "daily", "Radar Daily", "1. Vulnerability fix\n   Details")
    items = items_open(conn, bot="radar")
    item_id = items[0]["id"]

    act1 = create_action(conn, item_id, "reply", {"text": "Look into this"}, "daniel")
    act2 = create_action(conn, item_id, "priority", {"delta": -1}, "daniel")

    # Peek should return pending/queued actions without marking seen/processing
    peeked = inbox(conn, "radar", include_seen=False, mark_seen=False)
    assert len(peeked) == 2
    assert peeked[0]["status"] == "queued"
    assert peeked[1]["status"] == "pending"

    # Inbox call with mark_seen=True (default): queued -> processing, pending -> seen
    seen_inbox = inbox(conn, "radar", include_seen=False, mark_seen=True)
    assert len(seen_inbox) == 2
    assert seen_inbox[0]["status"] == "processing"
    assert seen_inbox[1]["status"] == "seen"
    assert seen_inbox[0]["seen_at"] is not None
    assert seen_inbox[0]["item"]["id"] == item_id
    assert seen_inbox[0]["item"]["url"] == f"{BRIEFS_PUBLIC_URL.rstrip('/')}/i/{item_id}"

    # Now pending/queued inbox should be empty
    empty_inbox = inbox(conn, "radar", include_seen=False, mark_seen=True)
    assert len(empty_inbox) == 0

    # With include_seen=True, should return both
    with_seen = inbox(conn, "radar", include_seen=True, mark_seen=True)
    assert len(with_seen) == 2

    # Ack act1 as applied
    acked1 = ack(conn, act1["id"], "applied", note="Fixed in commit 1234")
    assert acked1["status"] == "applied"
    assert acked1["bot_note"] == "Fixed in commit 1234"
    assert acked1["acked_at"] is not None

    # Ack act2 as failed
    acked2 = ack(conn, act2["id"], "failed", note="Cannot prioritize higher")
    assert acked2["status"] == "failed"

    # Invalid ack status
    with pytest.raises(ValueError, match="applied"):
        ack(conn, act1["id"], "invalid_status")

    # Note exceeding 2000 chars
    with pytest.raises(ValueError, match="2000"):
        ack(conn, act1["id"], "applied", note="x" * 2001)

    # Unknown action id
    with pytest.raises(ValueError, match="not found"):
        ack(conn, 999999, "applied")

    conn.close()


def test_wake_bot_with_stub_notify(temp_db: str, tmp_path: Path, monkeypatch) -> None:
    """Test wake_bot with stub notify command (configured -> sent, exit 1 -> failed, not configured)."""
    clear_notify_cache()
    stub_script = tmp_path / "stub_notify.sh"
    # Script behavior:
    # --list: prints "radar configured=yes\nchess configured=no\nfailed-bot configured=yes"
    # <bot> ...: if bot is "failed-bot", exit 1; else exit 0
    stub_script.write_text(
        """#!/bin/sh
if [ "$1" = "--list" ]; then
    echo "radar configured=yes"
    echo "chess configured=no"
    echo "failed-bot configured=yes"
    exit 0
fi
if [ "$1" = "failed-bot" ]; then
    exit 1
fi
exit 0
""",
        encoding="utf-8",
    )
    stub_script.chmod(stub_script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("BRIEFS_NOTIFY_CMD", str(stub_script))

    assert notify_configured("radar", use_cache=False) is True
    assert notify_configured("chess", use_cache=False) is False
    assert notify_configured("unknown-bot", use_cache=False) is False

    conn = connect(temp_db)
    # 1. Radar item -> sent
    upsert_report(conn, "radar", "2026-10-23", "daily", "Radar Daily", "1. Bug fix\n   Details")
    items = items_open(conn, bot="radar")
    act_radar = create_action(conn, items[0]["id"], "reply", {"text": "wake please", "wake": True}, "tester")

    stat1 = wake_bot(conn, act_radar["id"])
    assert stat1 == "sent"
    row1 = conn.execute("SELECT wake_status FROM actions WHERE id = ?", (act_radar["id"],)).fetchone()
    assert row1["wake_status"] == "sent"

    # 2. Chess item (not configured) -> not_configured
    upsert_report(conn, "chess", "2026-10-23", "daily", "Chess Daily", "1. Move review\n   Details")
    items_chess = items_open(conn, bot="chess")
    act_chess = create_action(conn, items_chess[0]["id"], "reply", {"text": "wake please", "wake": True}, "tester")

    stat2 = wake_bot(conn, act_chess["id"])
    assert stat2 == "not_configured"
    row2 = conn.execute("SELECT wake_status FROM actions WHERE id = ?", (act_chess["id"],)).fetchone()
    assert row2["wake_status"] == "not_configured"

    conn.close()


def test_web_csrf_and_post_actions(temp_db: str) -> None:
    """Test web CSRF protections (missing HX-Request, cross-origin, same-origin) and POST actions."""
    app = create_app(temp_db)
    conn = connect(temp_db)
    upsert_report(conn, "radar", "2026-10-23", "daily", "Radar Daily", "1. Item Alpha\n   Details")
    item_id = items_open(conn, bot="radar")[0]["id"]
    conn.close()

    client = TestClient(app, base_url="http://testserver")

    # 1. Missing HX-Request -> 403
    r = client.post(
        f"/i/{item_id}/action",
        data={"type": "done"},
        headers={"Tailscale-User-Login": "user@example.com", "Origin": "http://testserver"},
    )
    assert r.status_code == 403

    # 2. Cross-origin Origin -> 403
    r = client.post(
        f"/i/{item_id}/action",
        data={"type": "done"},
        headers={
            "Tailscale-User-Login": "user@example.com",
            "HX-Request": "true",
            "Origin": "http://evil.com",
        },
    )
    assert r.status_code == 403

    # 3. Same-origin -> 200
    r = client.post(
        f"/i/{item_id}/action",
        data={"type": "done"},
        headers={
            "Tailscale-User-Login": "user@example.com",
            "HX-Request": "true",
            "Origin": "http://testserver",
        },
    )
    assert r.status_code == 200
    assert "item-action-panel" in r.text
    assert "done" in r.text.lower()
    assert r.headers.get("HX-Trigger") == "itemUpdated"

    # 4. From-row done swap -> returns stub with Undo
    r_row = client.post(
        f"/i/{item_id}/action",
        data={"type": "done", "from_row": "1"},
        headers={
            "Tailscale-User-Login": "user@example.com",
            "HX-Request": "true",
            "Origin": "http://testserver",
        },
    )
    assert r_row.status_code == 200
    assert "item-done-stub" in r_row.text
    assert "Undo" in r_row.text

    # 5. Undo from stub
    r_undo = client.post(
        f"/i/{item_id}/action",
        data={"type": "reopen", "from_row": "1"},
        headers={
            "Tailscale-User-Login": "user@example.com",
            "HX-Request": "true",
            "Origin": "http://testserver",
        },
    )
    assert r_undo.status_code == 200

    # 6. Test snooze quick options: 1d, 3d, 1w
    for snooze_opt in ("1d", "3d", "1w"):
        r_snz = client.post(
            f"/i/{item_id}/action",
            data={"type": "snooze", "until": snooze_opt},
            headers={
                "Tailscale-User-Login": "user@example.com",
                "HX-Request": "true",
                "Origin": "http://testserver",
            },
        )
        assert r_snz.status_code == 200
        assert "snoozed until" in r_snz.text


def test_local_only_api_endpoints(temp_db: str) -> None:
    """Local-only endpoints reject proxied requests or requests with Tailscale-* headers."""
    app = create_app(temp_db)
    conn = connect(temp_db)
    upsert_report(conn, "radar", "2026-10-23", "daily", "Radar Daily", "1. Vulnerability fix\n   Details")
    item_id = items_open(conn, bot="radar")[0]["id"]
    act = create_action(conn, item_id, "reply", {"text": "test reply"}, "tester")
    conn.close()

    client = TestClient(app)

    # 1. GET /api/inbox with Tailscale header -> 403
    r = client.get("/api/inbox?bot=radar", headers={"Tailscale-User-Login": "user@example.com"})
    assert r.status_code == 403

    # 2. GET /api/inbox with X-Forwarded-For -> 403
    r = client.get("/api/inbox?bot=radar", headers={"X-Forwarded-For": "100.64.0.1"})
    assert r.status_code == 403

    # 3. Direct localhost GET /api/inbox -> 200
    r = client.get("/api/inbox?bot=radar")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["id"] == act["id"]

    # 4. POST /api/actions/{id}/ack with Tailscale header -> 403
    r = client.post(
        f"/api/actions/{act['id']}/ack",
        json={"status": "applied", "note": "bot ack"},
        headers={"Tailscale-User-Login": "user@example.com"},
    )
    assert r.status_code == 403

    # 5. Direct localhost POST /api/actions/{id}/ack -> 200
    r = client.post(
        f"/api/actions/{act['id']}/ack",
        json={"status": "applied", "note": "bot ack"},
    )
    assert r.status_code == 200
    res = r.json()
    assert res["status"] == "applied"
    assert res["bot_note"] == "bot ack"

    # 6. GET /api/items/{id}/actions -> 200
    r = client.get(f"/api/items/{item_id}/actions")
    assert r.status_code == 200
    actions_data = r.json()
    assert len(actions_data) >= 1


def test_cli_inbox_ack_and_publish_variations(tmp_path: Path, monkeypatch) -> None:
    """Test CLI commands: inbox, ack, actions, publish with --md + --items-json and --from-sheet-rows."""
    db_path = str(tmp_path / "cli_m2_test.db")

    # 1. Init DB
    monkeypatch.setattr("sys.argv", ["brief", "--db", db_path, "init"])
    main()

    # 2. Publish with --md + --items-json
    md_file = tmp_path / "body.md"
    md_file.write_text("# Radar Daily Custom Body\n\nCustom markdown content.", encoding="utf-8")

    items_json_file = tmp_path / "items.json"
    items_json_file.write_text(
        json.dumps([
            {"id": "1", "item": "Vulnerability scan", "details": "High severity CVE", "links": ["https://nvd.nist.gov/vuln/detail/CVE-2026-1234"]}
        ]),
        encoding="utf-8",
    )

    captured: list[str] = []
    monkeypatch.setattr("builtins.print", lambda msg: captured.append(str(msg)))
    monkeypatch.setattr(
        "sys.argv",
        [
            "brief",
            "--db",
            db_path,
            "publish",
            "--bot",
            "radar",
            "--date",
            "2026-10-23",
            "--md",
            str(md_file),
            "--items-json",
            str(items_json_file),
        ],
    )
    main()

    pub_res = json.loads(captured[-1])
    assert pub_res["status"] == "created"
    assert pub_res["items"] == 1

    # 3. Publish with --from-sheet-rows (5 cols with header and =HYPERLINK formula)
    sheet_rows_file = tmp_path / "sheet_rows.json"
    sheet_rows_data = [
        ["ID", "Item", "Status/details", "Your reply", "Links"],  # Header row to skip
        ["1", "Fix database connection leak", "Under review", "Reply from Daniel", '=HYPERLINK("https://github.com/org/repo/pull/123", "PR 123")'],
        ["2", "Update dependencies", "Weekly bump", "", "https://github.com/org/repo/issues/456"],
    ]
    sheet_rows_file.write_text(json.dumps(sheet_rows_data), encoding="utf-8")

    captured.clear()
    monkeypatch.setattr(
        "sys.argv",
        [
            "brief",
            "--db",
            db_path,
            "publish",
            "--bot",
            "coding",
            "--date",
            "2026-10-23",
            "--from-sheet-rows",
            str(sheet_rows_file),
        ],
    )
    main()

    sheet_pub_res = json.loads(captured[-1])
    assert sheet_pub_res["status"] == "created"
    assert sheet_pub_res["items"] == 2

    # Verify links were parsed from formula
    conn = connect(db_path)
    coding_items = items_open(conn, bot="coding")
    item_pr = next(it for it in coding_items if "leak" in it["title"].lower())
    assert len(item_pr["links"]) == 1
    assert item_pr["links"][0]["label"] == "PR 123"
    assert item_pr["links"][0]["url"] == "https://github.com/org/repo/pull/123"

    # Add an action for coding bot item
    act = create_action(conn, item_pr["id"], "reply", {"text": "Ship it!"}, "daniel")
    conn.close()

    # 4. CLI inbox --bot coding --json
    captured.clear()
    monkeypatch.setattr(
        "sys.argv",
        ["brief", "--db", db_path, "inbox", "--bot", "coding", "--json"],
    )
    main()
    inbox_data = json.loads(captured[-1])
    assert len(inbox_data) == 1
    assert inbox_data[0]["id"] == act["id"]
    assert inbox_data[0]["payload"]["text"] == "Ship it!"

    # 5. CLI ack
    captured.clear()
    monkeypatch.setattr(
        "sys.argv",
        ["brief", "--db", db_path, "ack", str(act["id"]), "--status", "applied", "--note", "Shipped!"],
    )
    main()
    ack_res = json.loads(captured[-1])
    assert isinstance(ack_res, list)
    assert len(ack_res) == 1
    assert ack_res[0]["status"] == "applied"
    assert ack_res[0]["bot_note"] == "Shipped!"

    # 6. CLI actions --item <id>
    captured.clear()
    monkeypatch.setattr(
        "sys.argv",
        ["brief", "--db", db_path, "actions", "--item", str(item_pr["id"])],
    )
    main()
    history_data = json.loads(captured[-1])
    assert len(history_data) >= 1
    assert history_data[0]["id"] == act["id"]

    # 7. Error handling: unknown bot fails with code 1
    with pytest.raises(SystemExit) as exc_info:
        monkeypatch.setattr(
            "sys.argv",
            ["brief", "--db", db_path, "inbox", "--bot", "unknown_bot_xyz"],
        )
        main()
    assert exc_info.value.code == 1


def test_importer_never_overwrites_bot_published_report(temp_db: str) -> None:
    conn = connect(temp_db)
    init_schema(conn)
    items = [{"display_id": "1", "title": "Keyed", "text_md": "x", "item_key": "radar:keyed", "position": 1, "links": [], "flags": set()}]
    rid, st, n = upsert_report(conn, "radar", "2026-10-20", "daily", "Bot published", "body", source="cli", items=items)
    assert st == "created" and n == 1
    rid2, st2, _ = upsert_report(conn, "radar", "2026-10-20", "daily", "File import", "1. Something else", source="file:/x.md")
    assert rid2 == rid and st2 == "unchanged"
    row = conn.execute("SELECT title, source FROM reports WHERE id = ?", (rid,)).fetchone()
    assert row["title"] == "Bot published" and row["source"] == "cli"
    keys = [r[0] for r in conn.execute("SELECT i.item_key FROM report_items ri JOIN items i ON i.id = ri.item_id WHERE ri.report_id = ?", (rid,))]
    assert keys == ["radar:keyed"]


def test_publish_items_json_without_ids(tmp_path: Path, monkeypatch, capsys) -> None:
    db = str(tmp_path / "p.db")
    rows = tmp_path / "items.json"
    rows.write_text(json.dumps([{"key": "gmail:abc", "title": "A"}, {"title": "B"}, {"id": "1", "title": "C"}]))
    md = tmp_path / "r.md"
    md.write_text("# t\n")
    monkeypatch.setattr("sys.stdin", open(os.devnull))
    monkeypatch.setattr("sys.argv", ["brief", "--db", db, "publish", "--bot", "one-offs", "--md", str(md), "--items-json", str(rows)])
    main()
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["items"] == 3
    conn = connect(db)
    ids = sorted(r[0] for r in conn.execute("SELECT display_id FROM report_items"))
    assert ids == ["1", "1-2", "2"]
