from __future__ import annotations

import json
from pathlib import Path

from briefs.db import connect, init_schema
from briefs.importer import run_import
from briefs.store import get_item, get_report, items_open, upsert_report


def test_store_idempotency(samples_path: Path, temp_db: str) -> None:
    sources = {
        "radar_md_glob": str(samples_path / "radar-*.md"),
        "zero_md_glob": str(samples_path / "zero-*.md"),
        "zero_state": str(samples_path / "zero-reported.json"),
        "family_state": str(samples_path / "family-state.json"),
        "cos_reqs": str(samples_path / "cos-reqs.json"),
        "transcripts": str(samples_path / "transcripts-sendtouser.json"),
    }

    # Run 1
    res1 = run_import(sources=sources, db_path=temp_db)
    # Check that some reports were created
    created_total_1 = sum(b["created"] for b in res1["summary"])
    assert created_total_1 > 0

    conn = connect(temp_db)
    items_count_1 = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    reports_count_1 = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
    conn.close()

    # Run 2
    res2 = run_import(sources=sources, db_path=temp_db)
    created_total_2 = sum(b["created"] for b in res2["summary"])
    updated_total_2 = sum(b["updated"] for b in res2["summary"])
    unchanged_total_2 = sum(b["unchanged"] for b in res2["summary"])

    assert created_total_2 == 0, "Second import run should create 0 new reports"
    assert updated_total_2 == 0, "Second import run should update 0 reports"
    assert unchanged_total_2 == reports_count_1, "All reports should be unchanged on second run"

    conn = connect(temp_db)
    items_count_2 = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    reports_count_2 = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
    conn.close()

    assert items_count_1 == items_count_2, "Items count must remain identical"
    assert reports_count_1 == reports_count_2, "Reports count must remain identical"


def test_carry_forward_matching(temp_db: str) -> None:
    conn = connect(temp_db)

    # Day 1 report
    body1 = "1. Music for the spring recital on Sunday (new)\n   Drafting music list for the recital."
    rep_id1, stat1, n1 = upsert_report(conn, "zero", "2026-10-20", "daily", "Zero Daily Oct 20", body1)
    assert stat1 == "created"

    items_day1 = items_open(conn, bot="zero")
    assert len(items_day1) == 1
    item1_id = items_day1[0]["id"]
    item1_key = items_day1[0]["item_key"]

    # Day 2 report (3 days later) with slightly varied title (e.g. 'recital concert on Sunday')
    body2 = "1. Music for the spring recital concert on Sunday (updated)\n   Drafting music list for the recital."
    rep_id2, stat2, n2 = upsert_report(conn, "zero", "2026-10-23", "daily", "Zero Daily Oct 23", body2)
    assert stat2 == "created"

    items_day2 = items_open(conn, bot="zero")
    # Should still be 1 item because it matched via carry-forward!
    assert len(items_day2) == 1
    item2_id = items_day2[0]["id"]
    assert item1_id == item2_id, "Item ID must be reused across days via carry-forward matching"

    # Meta aliases should contain the second day's key
    meta = items_day2[0]["meta"]
    assert len(meta.get("aliases", [])) >= 2


def test_stale_marking(temp_db: str) -> None:
    conn = connect(temp_db)

    # Report 1 has items A and B
    body1 = "1. First Item Alpha\n   Details A.\n2. Second Item Beta\n   Details B."
    upsert_report(conn, "radar", "2026-10-20", "daily", "Radar 10-20", body1)

    items1 = items_open(conn, bot="radar")
    assert len(items1) == 2
    assert not items1[0]["meta"].get("stale")
    assert not items1[1]["meta"].get("stale")

    # Report 2 only has item A
    body2 = "1. First Item Alpha\n   Details A."
    upsert_report(conn, "radar", "2026-10-22", "daily", "Radar 10-22", body2)

    items2 = items_open(conn, bot="radar")
    assert len(items2) == 2
    item_a = next(it for it in items2 if "Alpha" in it["title"])
    item_b = next(it for it in items2 if "Beta" in it["title"])

    assert not item_a["meta"].get("stale"), "Item in latest report should not be stale"
    assert item_b["meta"].get("stale"), "Item omitted from latest report must be marked stale"
