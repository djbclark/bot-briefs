from __future__ import annotations

import json
from pathlib import Path

from briefs.cli import main
from briefs.config import BRIEFS_PUBLIC_URL


def test_cli_publish_and_items(tmp_path: Path, monkeypatch) -> None:
    db_path = str(tmp_path / "cli_test.db")
    brief_file = tmp_path / "test_brief.md"
    brief_file.write_text(
        "**Money daily review**\n\n1. Check bank balance\n   Review checking account.\n2. Pay invoice\n   Due tomorrow.",
        encoding="utf-8",
    )

    # 1. brief init
    monkeypatch.setattr("sys.argv", ["brief", "--db", db_path, "init"])
    main()

    # 2. brief publish
    captured_json = []

    def fake_print(msg):
        captured_json.append(msg)

    monkeypatch.setattr("builtins.print", fake_print)
    monkeypatch.setattr(
        "sys.argv",
        [
            "brief",
            "--db",
            db_path,
            "publish",
            "--bot",
            "money",
            "--date",
            "2026-10-23",
            "--file",
            str(brief_file),
        ],
    )
    main()

    assert captured_json, "CLI publish should print output"
    pub_result = json.loads(captured_json[-1])
    assert pub_result["report_id"] == 1
    assert pub_result["status"] == "created"
    assert pub_result["items"] == 2
    assert pub_result["url"] == f"{BRIEFS_PUBLIC_URL.rstrip('/')}/r/1"

    # 3. brief items --open --json
    captured_items = []
    monkeypatch.setattr("builtins.print", lambda msg: captured_items.append(msg))
    monkeypatch.setattr(
        "sys.argv",
        ["brief", "--db", db_path, "items", "--bot", "money", "--open", "--json"],
    )
    main()

    items_list = json.loads(captured_items[-1])
    assert len(items_list) == 2
    assert any("bank balance" in it["title"].lower() for it in items_list)


def test_cli_publish_with_items_json(tmp_path: Path, monkeypatch) -> None:
    db_path = str(tmp_path / "cli_items_test.db")
    rows_file = tmp_path / "rows.json"
    rows_data = [
        ["1", "Item One", "Details for item one", ["https://github.com/example/repo"]],
        ["1.1", "Sub Item One", "Details for sub-item", [["Link Label", "https://example.com/sub"]]],
    ]
    rows_file.write_text(json.dumps(rows_data), encoding="utf-8")

    captured_json = []
    monkeypatch.setattr("builtins.print", lambda msg: captured_json.append(msg))
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
            "--title",
            "Radar Structured",
            "--items-json",
            str(rows_file),
        ],
    )
    main()

    res = json.loads(captured_json[-1])
    assert res["status"] == "created"
    assert res["items"] == 2


def test_items_json_explicit_and_link_keys() -> None:
    from briefs.cli import parse_items_json_data
    items = parse_items_json_data([
        ["1", "Reply to Bob", "waiting", ["https://mail.google.com/mail/u/0/#inbox/ABC123"]],
        ["2", "Pay bill", "due Fri", [], "money:bill-electric"],
        {"id": "3", "item": "Call school", "details": "x", "key": "fam:school", "due": "2026-10-30"},
    ])
    assert "mail.google.com" in items[0]["item_key"]
    assert items[1]["item_key"] == "money:bill-electric"
    assert items[2]["item_key"] == "fam:school" and items[2]["due_at"] == "2026-10-30"
