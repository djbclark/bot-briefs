"""Dual-run mirror: box -> VPS replication (briefs.mirror) and VPS -> box actions (briefs.syncapi). No network:
mirror.http is routed straight into a FastAPI TestClient running the VPS app."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from briefs import mirror
from briefs.db import connect, init_schema
from briefs.store import upsert_report


@pytest.fixture
def pair(tmp_path: Path, monkeypatch):
    box_db = str(tmp_path / "box.db")
    vps_db = str(tmp_path / "vps.db")
    token = tmp_path / "token"
    token.write_text("tok-123\n")
    for p in (box_db, vps_db):
        init_schema(connect(p)).close()
    monkeypatch.setenv("BRIEFS_SYNC_TOKEN_FILE", str(token))
    import importlib
    import briefs.web as web
    monkeypatch.setattr(web, "SYNC_TOKEN_FILE", str(token))
    app = web.create_app(vps_db)
    client = TestClient(app)

    def fake_http(method, path, body=None, timeout=25):
        r = client.request(method, path, json=body, headers={"Authorization": "Bearer tok-123"})
        assert r.status_code < 300, r.text
        return r.json()

    monkeypatch.setattr(mirror, "http", fake_http)
    monkeypatch.setattr(mirror, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(mirror, "STATUS_DIR", tmp_path / "status")
    st = mirror.state_db()
    return {"box": box_db, "vps": vps_db, "client": client, "st": st}


def box_publish(db, title="T", body="x"):
    conn = connect(db)
    upsert_report(conn, bot="radar", kind="daily", date="2026-10-31", title=title, body_md=body,
                  items=[{"id": "1", "title": "A", "text_md": body, "item_key": "k:a", "links": []}])
    conn.close()


def count(db, table):
    return sqlite3.connect(db).execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_token_required(pair):
    c = pair["client"]
    assert c.post("/sync/push", json={}).status_code == 401
    assert c.get("/sync/actions", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/healthz/mirror").status_code == 200  # timestamps only


def test_push_is_idempotent_and_follows_changes(pair):
    box_publish(pair["box"], body="first")
    box = mirror.open_box(pair["box"])
    assert mirror.push_cycle(box, pair["st"]) > 0
    assert mirror.push_cycle(box, pair["st"]) == 0  # nothing new
    assert count(pair["vps"], "reports") == count(pair["box"], "reports") == 1
    # replay: forget what was sent, resend everything -> no duplicates
    pair["st"].execute("DELETE FROM rowstate"); pair["st"].commit()
    mirror.push_cycle(box, pair["st"])
    assert count(pair["vps"], "items") == 1 and count(pair["vps"], "report_items") == 1
    box_publish(pair["box"], body="second")  # replaces report_items on the box
    mirror.push_cycle(box, pair["st"])
    v = sqlite3.connect(pair["vps"])
    assert v.execute("SELECT body_md FROM report_items").fetchall() == [("second",)]
    assert v.execute("SELECT COUNT(*) FROM items_fts WHERE items_fts MATCH 'second'").fetchone()[0] == 1
    assert pair["client"].get("/healthz/mirror").json()["stale"] is False


def test_vps_action_reaches_box_exactly_once_and_ack_shows_on_both(pair):
    box_publish(pair["box"])
    box = mirror.open_box(pair["box"])
    mirror.push_cycle(box, pair["st"])
    vc = connect(pair["vps"])
    from briefs.actions import create_action
    a = create_action(vc, 1, "reply", {"text": "hello"}, "user@example.com", status="submitted")
    assert a["id"] >= 1_000_000_000
    b = create_action(vc, 1, "done", {}, "user@example.com")
    vc.close()
    assert mirror.pull_cycle(pair["box"]) == 2
    assert mirror.pull_cycle(pair["box"]) == 0  # replay finds nothing
    bc = sqlite3.connect(pair["box"])
    assert bc.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 2
    assert bc.execute("SELECT status FROM items WHERE id=1").fetchone()[0] == "done"  # effect applied on box
    # a crash-replay of the same VPS action must not create a second box action
    entry = {"vps_id": a["id"], "mapped": False, "action": dict(a, payload_json=a["payload_json"]), "version": "x"}
    mirror.apply_vps_action(mirror.open_box(pair["box"]), entry)
    assert bc.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 2
    # VPS renumbered to box ids; ack on the box shows on the VPS after the next push
    vids = [r[0] for r in sqlite3.connect(pair["vps"]).execute("SELECT id FROM actions ORDER BY id")]
    assert vids == [1, 2]
    from briefs.actions import ack
    ack(mirror.open_box(pair["box"]), 1, "applied", "done it")
    mirror.push_cycle(box, pair["st"])
    assert sqlite3.connect(pair["vps"]).execute("SELECT status, bot_note FROM actions WHERE id=1").fetchone() == ("applied", "done it")
    assert pair["client"].get("/sync/status", headers={"Authorization": "Bearer tok-123"}).json()["pending_for_box"] == 0


def test_box_is_authoritative_after_bot_takes_action(pair):
    box_publish(pair["box"])
    box = mirror.open_box(pair["box"])
    mirror.push_cycle(box, pair["st"])
    vc = connect(pair["vps"])
    from briefs.actions import queue_text, submit_all
    queue_text(vc, 1, "draft", "user@example.com")
    mirror.pull_cycle(pair["box"])
    from briefs.actions import ack, mark
    bc = mirror.open_box(pair["box"])
    mark(bc, 1, "processing")
    submit_all(vc, [], "user@example.com")  # User presses Submit all on the VPS: queued -> submitted
    mirror.pull_cycle(pair["box"])
    assert bc.execute("SELECT status FROM actions WHERE id=1").fetchone()[0] == "processing"  # box wins
    mirror.push_cycle(box, pair["st"])
    assert sqlite3.connect(pair["vps"]).execute("SELECT status FROM actions WHERE id=1").fetchone()[0] == "processing"


def test_unpulled_vps_item_change_survives_box_push(pair):
    box_publish(pair["box"])
    box = mirror.open_box(pair["box"])
    mirror.push_cycle(box, pair["st"])
    vc = connect(pair["vps"])
    from briefs.actions import create_action
    create_action(vc, 1, "done", {}, "user@example.com")
    box_publish(pair["box"], body="touched")  # box changes the item row meanwhile (last_seen etc.)
    bc = connect(pair["box"]); bc.execute("UPDATE items SET title='A2' WHERE id=1"); bc.commit()
    mirror.push_cycle(box, pair["st"])
    v = sqlite3.connect(pair["vps"])
    assert v.execute("SELECT title, status FROM items WHERE id=1").fetchone() == ("A2", "done")


def test_backfill_snapshot_seeds_state(pair, tmp_path):
    box_publish(pair["box"])
    import argparse
    mirror.BRIEFS_DB = pair["box"]
    out = tmp_path / "snap.db"
    mirror.cmd_backfill(argparse.Namespace(out=str(out)))
    assert count(str(out), "items") == 1
    box = mirror.open_box(pair["box"])
    assert mirror.push_cycle(box, pair["st"]) == 0  # nothing left to send after seeding from the snapshot


def test_stale_banner_shows_after_30_minutes(pair):
    c = pair["client"]
    assert "no update from the main dashboard box" in c.get("/").text  # never pushed yet -> stale
    mirror.http("POST", "/sync/push", {"heartbeat": True})
    assert "no update from the main dashboard box" not in c.get("/").text
    vc = connect(pair["vps"])
    vc.execute("UPDATE sync_meta SET v='2020-01-01T00:00:00+00:00' WHERE k='last_push_at'"); vc.commit()
    assert "no update from the main dashboard box" in c.get("/").text
