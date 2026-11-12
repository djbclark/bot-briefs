from __future__ import annotations

import json
from pathlib import Path
import pytest

from briefs.parse import clean_title, compute_item_key, extract_links, normalize_url, parse_brief, parse_cos_reqs


def test_parse_radar_samples(samples_path: Path) -> None:
    r23_file = samples_path / "radar-2026-10-23.md"
    if not r23_file.exists():
        pytest.skip("radar-2026-10-23.md missing")

    md = r23_file.read_text(encoding="utf-8")
    parsed = parse_brief(md, default_date="2026-10-23")

    assert parsed["items"], "Should parse items from radar-2026-10-23"
    assert len(parsed["items"]) >= 10
    assert parsed["kind_hint"] == "weekly"
    assert parsed["date_hint"] == "2026-10-23"
    assert parsed["sheet_url"] is not None
    assert "docs.google.com/spreadsheets" in parsed["sheet_url"]

    # Verify display IDs and sub-items
    disp_ids = [it["display_id"] for it in parsed["items"]]
    assert "1" in disp_ids
    assert "1.1" in disp_ids
    assert "1.2" in disp_ids

    # Check parent display IDs
    sub_1_1 = next(it for it in parsed["items"] if it["display_id"] == "1.1")
    assert sub_1_1["parent_display_id"] == "1"


def test_parse_zero_samples(samples_path: Path) -> None:
    z22_file = samples_path / "zero-2026-10-22.md"
    z23_file = samples_path / "zero-2026-10-23.md"
    if not z22_file.exists() or not z23_file.exists():
        pytest.skip("Zero sample files missing")

    z22_parsed = parse_brief(z22_file.read_text(encoding="utf-8"), default_date="2026-10-22")
    assert len(z22_parsed["items"]) > 20
    assert z22_parsed["sheet_url"] is not None
    assert "docs.google.com/spreadsheets" in z22_parsed["sheet_url"]

    z23_parsed = parse_brief(z23_file.read_text(encoding="utf-8"), default_date="2026-10-23")
    assert len(z23_parsed["items"]) > 30
    assert z23_parsed["sheet_url"] is not None


def test_item_keys_stable_when_display_number_changes() -> None:
    text1 = "1. [Hyades](https://forgejo.mit.edu/SIPB/hyades): work on libkrun shim\n   Some extra details here."
    text2 = "8. [Hyades](https://forgejo.mit.edu/SIPB/hyades): work on libkrun shim\n   Some extra details here."

    p1 = parse_brief(text1)["items"][0]
    p2 = parse_brief(text2)["items"][0]

    assert p1["display_id"] == "1"
    assert p2["display_id"] == "8"
    assert p1["item_key"] == p2["item_key"]
    assert p1["item_key"] == "https://forgejo.mit.edu/SIPB/hyades"

    # Test hash-based title keys
    h_text1 = "1. Music for the spring recital on Sunday (updated)"
    h_text2 = "5. Music for the spring recital on Sunday (new)"

    hp1 = parse_brief(h_text1)["items"][0]
    hp2 = parse_brief(h_text2)["items"][0]

    assert hp1["item_key"] == hp2["item_key"]
    assert hp1["item_key"].startswith("h:")


def test_parse_cos_reqs(samples_path: Path) -> None:
    cos_file = samples_path / "cos-reqs.json"
    if not cos_file.exists():
        pytest.skip("cos-reqs.json missing")

    reqs_data = json.loads(cos_file.read_text(encoding="utf-8"))
    cos_parsed = parse_cos_reqs(reqs_data, default_date="2026-10-23")

    assert cos_parsed["title"] == "Chief of Staff"
    assert cos_parsed["kind"] == "cos"
    assert len(cos_parsed["items"]) > 25

    # Check rows with links
    linked_items = [it for it in cos_parsed["items"] if it["links"]]
    assert len(linked_items) > 5

    # Check hierarchical display IDs
    sub_items = [it for it in cos_parsed["items"] if it["parent_display_id"]]
    assert len(sub_items) > 10
    for sub in sub_items:
        assert sub["parent_display_id"] in [it["display_id"] for it in cos_parsed["items"]]


def test_date_only_titles_do_not_collide() -> None:
    from briefs.parse import compute_item_key
    a = compute_item_key("", "Sunday Oct 25")
    b = compute_item_key("", "Mon 10/26:")
    assert a != b and a != "h:da39a3ee5e6b"


def test_gmail_thread_urls_normalize_to_gmail_keys() -> None:
    from briefs.parse import rewrite_gmail_link_for_display
    tid = "1a1037c65622cc27"
    key = f"gmail:{tid}"
    account = "user@example.com"
    canon = f"https://dodosend.com/t/{tid}?account={account}"
    inputs = [
        f"https://mail.google.com/mail/u/0/#all/{tid}",
        canon,
        f"https://dodo-link.pages.dev/t/{tid}",
        f"dodosend://thread/{tid}",
        f"dodosend://thread/{tid}?account={account}",
    ]
    for url in inputs:
        assert normalize_url(url) == key
        assert compute_item_key(f"1. See [t]({url}) please", "See t please") == key
        assert rewrite_gmail_link_for_display(url).startswith(f"https://dodosend.com/t/{tid}?account=")
    assert rewrite_gmail_link_for_display(canon) == canon
    search = "https://mail.google.com/mail/u/0/#search/from%3Aa%40b.com"
    assert rewrite_gmail_link_for_display(search) == search
    extracted = extract_links(f"see [x](dodosend://thread/{tid})")
    assert extracted[0]["url"] == f"dodosend://thread/{tid}"
    assert compute_item_key(f"see [x](dodosend://thread/{tid})", "see x") == key
