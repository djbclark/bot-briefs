"""Tests for the local /todo-review page."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from briefs import todo_review as tr
from briefs.web import create_app

OK = {"HX-Request": "true", "Origin": "http://testserver", "Tailscale-User-Login": "user@example.com"}
XSS = "<script>alert(1)</script>"


def _rows():
    return [
        {"key": "saner-task:a", "title": "Old task", "source": "Saner", "created_at": "2026-01-01T12:00:00Z", "due_at": None,
         "status": "open", "links": [], "proposed_owner": "Money", "provenance": [{"saner_task_id": "a"}]},
        {"key": "saner-task:b", "title": XSS, "source": "Saner", "created_at": None, "due_at": None, "status": "completed",
         "links": ["javascript:alert(1)", "https://mail.google.com/mail/u/0/#search/from%3Aa%40b.com"],
         "proposed_owner": "Bogus", "provenance": [{"saner_task_id": "b"}]},
        {"key": "saner-task:c", "title": "Newer", "source": "Saner", "created_at": "2026-06-01T12:00:00Z", "due_at": None,
         "status": "open", "links": [], "proposed_owner": "Chess", "provenance": []},
    ]


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    p = tmp_path / "c.json"
    def write(rows):
        p.write_text(json.dumps({"manifest": {"complete": False, "exported_at": "2026-11-07T07:00:00-05:00"}, "rows": rows}))
    write(_rows())
    monkeypatch.setenv("BRIEFS_TODO_REVIEW_DATASET", str(p))
    tr._sync_cache.clear()
    db = str(tmp_path / "t.db")
    c = TestClient(create_app(db))
    c.write = write
    c.db = db
    return c


def decide(c, key="saner-task:a", **kw):
    return c.post("/todo-review/decide", data={"task_key": key, **kw}, headers=OK)


def count(c, table):
    return sqlite3.connect(c.db).execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_page_nav_warning_and_xss(env):
    r = env.get("/todo-review")
    assert r.status_code == 200
    assert "incomplete" in r.text and 'href="/todo-review"' in r.text
    assert XSS not in r.text and "&lt;script&gt;" in r.text
    assert "javascript:" not in r.text
    assert "mail.google.com" in r.text
    assert "Bogus" not in r.text


def test_sort_unknown_last_and_filters(env):
    env.get("/todo-review")
    conn = sqlite3.connect(env.db); conn.row_factory = sqlite3.Row
    rows = tr.load_rows_from_db(conn)
    assert len(rows) == 3
    order = [r["task_key"] for r in tr.filter_and_sort_rows(rows, sort="age_desc")]
    assert order == ["saner-task:a", "saner-task:c", "saner-task:b"]
    assert [r["task_key"] for r in tr.filter_and_sort_rows(rows, owner="Money")] == ["saner-task:a"]
    assert [r["task_key"] for r in tr.filter_and_sort_rows(rows, status="completed")] == ["saner-task:b"]
    assert [r["task_key"] for r in tr.filter_and_sort_rows(rows, owner="Other")] == ["saner-task:b"]
    assert len(tr.filter_and_sort_rows(rows, decision="undecided")) == 3
    assert "unknown" in [r for r in rows if r["task_key"] == "saner-task:b"][0]["created_et"].lower()


def test_auth_and_csrf(env):
    d = {"task_key": "saner-task:a", "decision": "keep"}
    env.get("/todo-review")
    assert env.post("/todo-review/decide", data=d, headers={k: v for k, v in OK.items() if k != "Tailscale-User-Login"}).status_code == 403
    assert env.post("/todo-review/decide", data=d, headers={k: v for k, v in OK.items() if k != "HX-Request"}).status_code == 403
    assert env.post("/todo-review/decide", data=d, headers={**OK, "Origin": "http://evil.example"}).status_code == 403
    assert count(env, "todo_review_decisions") == 0


def test_validation(env):
    env.get("/todo-review")
    assert decide(env, decision="bogus").status_code == 400
    assert decide(env, decision="keep", owner="Nobody").status_code == 400
    assert decide(env, key="saner-task:zzz", decision="keep").status_code == 404
    assert decide(env, key="../x y", decision="keep").status_code == 400


def test_persistence_idempotency_audit_reimport(env):
    env.get("/todo-review")
    assert decide(env, decision="changed", note="n1", owner="Zero", audit_label="x").headers["X-Todo-Review-Noop"] == "0"
    assert count(env, "todo_review_audit") == 1
    assert decide(env, decision="changed", note="n1", owner="Zero").headers["X-Todo-Review-Noop"] == "1"
    assert count(env, "todo_review_audit") == 1
    # reimport with changed export must keep the decision
    env.write(_rows() + [{"key": "saner-task:d", "title": "New", "source": "Saner", "status": "open", "links": [], "proposed_owner": "Other", "provenance": []}])
    r = env.get("/todo-review")
    assert "New" in r.text
    conn = sqlite3.connect(env.db); conn.row_factory = sqlite3.Row
    row = [x for x in tr.load_rows_from_db(conn) if x["task_key"] == "saner-task:a"][0]
    assert (row["decision"], row["note"], row["effective_owner"]) == ("changed", "n1", "Zero")
    assert env.get("/todo-review?decision=changed").text.count('class="tr-row"') == 1
    # clear reverts, with audit
    decide(env, decision="clear")
    assert count(env, "todo_review_audit") == 2


def test_no_dispatch_and_no_other_writes(env, monkeypatch):
    import socket
    def boom(*a, **k): raise AssertionError("network used")
    monkeypatch.setattr(socket.socket, "connect", boom)
    env.get("/todo-review"); decide(env, decision="done")
    conn = sqlite3.connect(env.db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"todo_review_items", "todo_review_decisions", "todo_review_audit"} <= tables
    if "items" in tables:
        assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0


def test_todo_safe_url_guard():
    assert tr.todo_safe_url("dodosend://thread/0123456789abcdef") == "dodosend://thread/0123456789abcdef"
    assert tr.todo_safe_url("dodosend://thread/0123456789abcdef?account=user@example.com").startswith("dodosend://")
    assert tr.todo_safe_url("dodosend://evil/x") == "#"
    assert tr.todo_safe_url("javascript:alert(1)") == "#"
    assert tr.todo_safe_url("https://mail.google.com/mail/u/0/#all/0123456789abcdef").startswith("https://")


def test_owner_override(monkeypatch):
    monkeypatch.setenv("BRIEFS_TODO_REVIEW_OWNERS", "Alpha, Beta,Other,Alpha")
    assert tr._load_owners() == ("Alpha", "Beta", "Other")
    monkeypatch.delenv("BRIEFS_TODO_REVIEW_OWNERS")
    assert tr._load_owners()[-1] == "Other" and "Other" not in tr._load_owners()[:-1]
