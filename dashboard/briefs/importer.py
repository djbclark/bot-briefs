from __future__ import annotations

import argparse
import difflib
import glob
import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from briefs.config import (
    BOTS,
    BRIEFS_DB,
    BRIEFS_SOURCES_DIR,
    REPO_ROOT,
    TZ_ET,
    find_bot,
    today_et,
)
from briefs.db import connect, init_schema
from briefs.parse import (
    clean_title,
    compute_item_key,
    extract_flags,
    extract_links,
    parse_brief,
    parse_cos_reqs,
)
from briefs.store import upsert_report


def get_default_sources() -> dict[str, str]:
    """Resolve default sources based on environment or existing sample paths."""
    samples_dir = REPO_ROOT / "dev" / "samples"
    use_samples = False

    if BRIEFS_SOURCES_DIR:
        src_dir = Path(BRIEFS_SOURCES_DIR)
        if not src_dir.is_absolute():
            src_dir = REPO_ROOT / src_dir
        return {
            "radar_md_glob": str(src_dir / "radar-*.md"),
            "zero_md_glob": str(src_dir / "zero-*.md"),
            "zero_state": str(src_dir / "zero-reported.json"),
            "family_state": str(src_dir / "family-state.json"),
            "cos_reqs": str(src_dir / "cos-reqs.json"),
            "transcripts": str(src_dir / "transcripts-sendtouser.json"),
        }

    # If /workspace paths don't exist, fall back to dev/samples
    if not os.path.exists("/workspace") and samples_dir.exists():
        return {
            "radar_md_glob": str(samples_dir / "radar-*.md"),
            "zero_md_glob": str(samples_dir / "zero-*.md"),
            "zero_state": str(samples_dir / "zero-reported.json"),
            "family_state": str(samples_dir / "family-state.json"),
            "cos_reqs": str(samples_dir / "cos-reqs.json"),
            "transcripts": str(samples_dir / "transcripts-sendtouser.json"),
        }

    return {
        "radar_md_glob": "/workspace/radar-????-??-??.md",
        "zero_md_glob": "/workspace/triage-state/reports/????-??-??.md",
        "zero_state": "/workspace/triage-state/reported.json",
        "family_state": "/home/box/agent-data/family-digest/state.json",
        "cos_reqs": "/workspace/cos/reqs.json",
        "transcripts": str(REPO_ROOT / "data" / "sources" / "transcripts-sendtouser.json"),
    }


def _parse_priority(val: Any) -> Optional[int]:
    """Parse priority value (1, 'high', 'P1', etc.) to an integer."""
    if val is None:
        return None
    if isinstance(val, int):
        return val
    s = str(val).strip().lower()
    if s in ("1", "p1", "high"):
        return 1
    if s in ("2", "p2", "med", "medium"):
        return 2
    if s in ("3", "p3", "low"):
        return 3
    return None


def run_import(
    sources: Optional[dict[str, str]] = None,
    since: Optional[date] = None,
    dry_run: bool = False,
    db_path: Optional[str] = None,
) -> dict[str, Any]:
    """
    Execute the importer across configured sources.
    Returns a summary dict by bot with created/updated/unchanged counts,
    items total/open, and date ranges.
    """
    src = get_default_sources()
    if sources:
        src.update(sources)

    if since is None:
        # Default: today - 14 days
        today_date = datetime.strptime(today_et(), "%Y-%m-%d").date()
        since = today_date - timedelta(days=14)
    since_str = since.strftime("%Y-%m-%d")

    conn = connect(db_path or BRIEFS_DB)
    init_schema(conn)

    # Tracking metrics per bot
    bot_stats: dict[str, dict[str, Any]] = {
        b.slug: {
            "name": b.name,
            "created": 0,
            "updated": 0,
            "unchanged": 0,
            "reports_total": 0,
            "min_date": None,
            "max_date": None,
        }
        for b in BOTS
    }

    def record_report(bot_slug: str, rep_date: str, status: str) -> None:
        stats = bot_stats[bot_slug]
        if status in ("created", "updated", "unchanged"):
            stats[status] += 1
            stats["reports_total"] += 1
            if not stats["min_date"] or rep_date < stats["min_date"]:
                stats["min_date"] = rep_date
            if not stats["max_date"] or rep_date > stats["max_date"]:
                stats["max_date"] = rep_date

    # 1. Load Zero reported.json state if available
    zero_state_items: list[dict[str, Any]] = []
    zero_state_path = src.get("zero_state")
    if zero_state_path and os.path.exists(zero_state_path):
        try:
            with open(zero_state_path, "r", encoding="utf-8") as f:
                zdata = json.load(f)
                zero_state_items = zdata.get("items", [])
        except Exception:
            pass

    # 2. Load Family state.json if available
    family_state_items: list[dict[str, Any]] = []
    family_state_path = src.get("family_state")
    if family_state_path and os.path.exists(family_state_path):
        try:
            with open(family_state_path, "r", encoding="utf-8") as f:
                fdata = json.load(f)
                family_state_items = fdata.get("open_items", [])
        except Exception:
            pass

    matched_zero_state_keys: set[str] = set()
    matched_family_sources: set[str] = set()

    def enrich_zero_items(items: list[dict[str, Any]]) -> None:
        for it in items:
            t = it["title"].lower()
            best_rep = None
            best_score = 0.0
            for rep in zero_state_items:
                cand_t = clean_title(rep.get("title", "")).lower()
                ratio = difflib.SequenceMatcher(None, t, cand_t).ratio()
                if ratio > best_score:
                    best_score = ratio
                    best_rep = rep

            if best_rep and best_score >= 0.55:
                rep_key = best_rep.get("key", "")
                thread_id = best_rep.get("threadId")
                matched_zero_state_keys.add(rep_key)

                if thread_id:
                    it["item_key"] = f"gmail:{thread_id.lower()}"
                elif rep_key:
                    it["item_key"] = f"zero:{rep_key}"

                prio = best_rep.get("priority")
                if prio:
                    it["priority"] = 1 if prio == "high" else (3 if prio == "low" else 2)

                if best_rep.get("deadline"):
                    it["due_at"] = best_rep["deadline"]

                if best_rep.get("resolved"):
                    it.setdefault("flags", set()).add("resolved")
                    it["status"] = "resolved"

    def enrich_family_items(items: list[dict[str, Any]]) -> None:
        for it in items:
            t = it["title"].lower()
            best_fam = None
            best_score = 0.0
            for fam in family_state_items:
                cand_t = clean_title(fam.get("item", "")).lower()
                ratio = difflib.SequenceMatcher(None, t, cand_t).ratio()
                if ratio > best_score:
                    best_score = ratio
                    best_fam = fam

            if best_fam and best_score >= 0.55:
                src_str = best_fam.get("source", "")
                m = re.search(r"([0-9a-fA-F]{16})", src_str)
                if m:
                    it["item_key"] = f"gmail:{m.group(1).lower()}"
                matched_family_sources.add(src_str)

                p = _parse_priority(best_fam.get("priority"))
                if p is not None:
                    it["priority"] = p

                if best_fam.get("deadline"):
                    it["due_at"] = best_fam["deadline"]

                if best_fam.get("resolved"):
                    it.setdefault("flags", set()).add("resolved")
                    it["status"] = "resolved"

    # 3. Transcripts (transcripts-sendtouser.json)
    transcripts_path = src.get("transcripts")
    candidate_transcripts: dict[tuple[str, str, str], dict[str, Any]] = {}

    if transcripts_path and os.path.exists(transcripts_path):
        try:
            with open(transcripts_path, "r", encoding="utf-8") as f:
                tdata = json.load(f)
            for entry in tdata:
                body = entry.get("body", "")
                if len(body) < 300:
                    continue

                has_num_list = bool(re.search(r"^\s{0,1}\d+\.\s", body, re.M))
                has_sheet = "docs.google.com/spreadsheets" in body
                lines = [l.strip() for l in body.splitlines() if l.strip()]
                first_line = lines[0] if lines else ""
                is_bold_title = bool(re.match(r"^\*\*.*?\*\*", first_line)) and any(
                    w in first_line.lower() for w in ["daily", "weekly", "review", "roundup", "brief"]
                )

                if not (has_num_list and (has_sheet or is_bold_title)):
                    continue

                bot_obj = find_bot(entry.get("agent")) or find_bot(entry.get("name"))
                if not bot_obj:
                    continue

                ts_str = entry.get("ts", "")
                try:
                    dt = datetime.fromisoformat(ts_str)
                except Exception:
                    continue

                parsed = parse_brief(body)
                # The turn timestamp bounds the date: a brief can't be dated after the turn it was
                # sent in, and a sheet/title date older than a week means a mis-parse.
                ts_date = dt.date()
                date_hint = parsed["date_hint"]
                if date_hint:
                    try:
                        hd = date.fromisoformat(date_hint)
                    except ValueError:
                        hd = None
                    if hd is None or hd > ts_date or (ts_date - hd).days > 7:
                        date_hint = None
                if not date_hint:
                    if dt.hour < 12:
                        date_hint = ts_date.isoformat()
                    else:
                        continue
                if not parsed.get("title_explicit"):
                    d_obj = date.fromisoformat(date_hint)
                    parsed["title"] = f"{bot_obj.name} {parsed['kind_hint']}, {d_obj.strftime('%a %b')} {d_obj.day}"

                if date_hint < since_str:
                    continue

                kind = parsed["kind_hint"]
                key = (bot_obj.id, date_hint, kind)

                if key not in candidate_transcripts or candidate_transcripts[key]["ts"] < ts_str:
                    candidate_transcripts[key] = {
                        "bot": bot_obj,
                        "date": date_hint,
                        "kind": kind,
                        "title": parsed["title"],
                        "body": body,
                        "sheet_url": parsed["sheet_url"],
                        "items": parsed["items"],
                        "ts": ts_str,
                    }
        except Exception:
            pass

    # Check which (bot, date, kind) are covered by explicit files
    file_covered_keys: set[tuple[str, str, str]] = set()

    # Pre-scan radar files
    radar_glob = src.get("radar_md_glob")
    if radar_glob:
        for p in glob.glob(radar_glob):
            m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(p))
            if m:
                d = m.group(1)
                radar_bot = find_bot("radar")
                try:
                    with open(p, "r", encoding="utf-8") as fh:
                        _r_items = parse_brief(fh.read(), default_date=d)["items"]
                        _radar_has_items = bool(_r_items) and not any(i.get("section") in ("Needs operator", "Needs you") for i in _r_items)
                except OSError:
                    _radar_has_items = False
                if radar_bot and _radar_has_items:
                    file_covered_keys.add((radar_bot.id, d, "daily"))
                    file_covered_keys.add((radar_bot.id, d, "weekly"))

    # Pre-scan zero files
    zero_glob = src.get("zero_md_glob")
    if zero_glob:
        for p in glob.glob(zero_glob):
            m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(p))
            if m:
                d = m.group(1)
                zero_bot = find_bot("zero")
                if zero_bot:
                    file_covered_keys.add((zero_bot.id, d, "daily"))
                    file_covered_keys.add((zero_bot.id, d, "weekly"))

    # Upsert transcript briefs (unless covered by file)
    for (bot_id, rep_date, kind), cand in sorted(candidate_transcripts.items(), key=lambda x: x[0][1]):
        if (bot_id, rep_date, kind) in file_covered_keys:
            continue

        bot_obj = cand["bot"]
        items = cand["items"]

        if bot_obj.slug == "zero":
            enrich_zero_items(items)
        elif bot_obj.slug == "family":
            enrich_family_items(items)

        if not dry_run:
            _, status, _ = upsert_report(
                conn,
                bot=bot_obj,
                date=rep_date,
                kind=kind,
                title=cand["title"],
                body_md=cand["body"],
                sheet_url=cand["sheet_url"],
                source="transcript",
                source_ref=f"{bot_obj.slug}:{rep_date}:{cand['ts']}",
                items=items,
            )
        else:
            status = "created"

        record_report(bot_obj.slug, rep_date, status)

    # 4. Radar markdown files
    if radar_glob:
        radar_bot = find_bot("radar")
        for p in sorted(glob.glob(radar_glob)):
            m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(p))
            if not m:
                continue
            rep_date = m.group(1)
            if rep_date < since_str:
                continue

            with open(p, "r", encoding="utf-8") as f:
                md_content = f.read()

            parsed = parse_brief(md_content, default_date=rep_date)
            kind = parsed["kind_hint"]
            title = parsed["title"]
            sheet_url = parsed["sheet_url"]
            items = parsed["items"]
            # Compact state notes (no items) lose to the brief actually sent in chat for that date.
            inline_only = (not items) or all(i.get("section") in ("Needs operator", "Needs you") for i in items)
            if inline_only and any(k[0] == radar_bot.id and k[1] == rep_date for k in candidate_transcripts):
                continue

            if not dry_run:
                _, status, _ = upsert_report(
                    conn,
                    bot=radar_bot,
                    date=rep_date,
                    kind=kind,
                    title=title,
                    body_md=md_content,
                    sheet_url=sheet_url,
                    source=f"file:{p}",
                    source_ref=os.path.basename(p),
                    items=items,
                )
            else:
                status = "created"

            record_report(radar_bot.slug, rep_date, status)

    # 5. Zero markdown files
    if zero_glob:
        zero_bot = find_bot("zero")
        for p in sorted(glob.glob(zero_glob)):
            m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(p))
            if not m:
                continue
            rep_date = m.group(1)
            if rep_date < since_str:
                continue

            with open(p, "r", encoding="utf-8") as f:
                md_content = f.read()

            parsed = parse_brief(md_content, default_date=rep_date)
            kind = parsed["kind_hint"]
            title = parsed["title"]
            sheet_url = parsed["sheet_url"]
            items = parsed["items"]
            enrich_zero_items(items)

            if not dry_run:
                _, status, _ = upsert_report(
                    conn,
                    bot=zero_bot,
                    date=rep_date,
                    kind=kind,
                    title=title,
                    body_md=md_content,
                    sheet_url=sheet_url,
                    source=f"file:{p}",
                    source_ref=os.path.basename(p),
                    items=items,
                )
            else:
                status = "created"

            record_report(zero_bot.slug, rep_date, status)

    # 6. Extra items from Zero reported.json not matched in any report
    if not dry_run:
        zero_bot = find_bot("zero")
        for rep in zero_state_items:
            rep_key = rep.get("key", "")
            if rep_key in matched_zero_state_keys:
                continue
            last_reported = rep.get("lastReported") or rep.get("firstSeen") or today_et()
            if last_reported < since_str:
                continue

            thread_id = rep.get("threadId")
            item_key = f"gmail:{thread_id.lower()}" if thread_id else f"zero:{rep_key}"
            title = rep.get("title", "")
            is_resolved = bool(rep.get("resolved"))
            status = "resolved" if is_resolved else "open"
            prio = 1 if rep.get("priority") == "high" else (3 if rep.get("priority") == "low" else 2)
            first_seen = rep.get("firstSeen") or last_reported

            # Upsert into items table directly if not present
            ex = conn.execute("SELECT id FROM items WHERE bot_id = ? AND item_key = ?", (zero_bot.id, item_key)).fetchone()
            if not ex:
                meta = {"aliases": [item_key], "from_state": True}
                conn.execute(
                    """
                    INSERT INTO items (bot_id, item_key, title, status, priority, area, due_at, first_seen, last_seen, links_json, meta_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        zero_bot.id,
                        item_key,
                        clean_title(title),
                        status,
                        prio,
                        zero_bot.area_default,
                        rep.get("deadline"),
                        first_seen,
                        last_reported,
                        "[]",
                        json.dumps(meta),
                    ),
                )

    # 7. Chief of Staff reqs.json
    cos_path = src.get("cos_reqs")
    if cos_path and os.path.exists(cos_path):
        cos_bot = find_bot("cos")
        try:
            with open(cos_path, "r", encoding="utf-8") as f:
                cos_data = json.load(f)

            # Date from file mtime in America/New_York
            mtime = os.path.getmtime(cos_path)
            cos_date = datetime.fromtimestamp(mtime, TZ_ET).strftime("%Y-%m-%d")

            cos_parsed = parse_cos_reqs(cos_data, default_date=cos_date)

            if not dry_run:
                _, status, _ = upsert_report(
                    conn,
                    bot=cos_bot,
                    date=cos_date,
                    kind="cos",
                    title=cos_parsed["title"],
                    body_md=cos_parsed["body_md"],
                    sheet_url=cos_parsed["sheet_url"],
                    source=f"file:{cos_path}",
                    source_ref="cos-reqs",
                    items=cos_parsed["items"],
                )
            else:
                status = "created"

            record_report(cos_bot.slug, cos_date, status)
        except Exception:
            pass

    # 8. Compute total & open items per bot for the summary table
    summary_results: list[dict[str, Any]] = []
    for b in BOTS:
        slug = b.slug
        stats = bot_stats[slug]

        total_items = 0
        open_items = 0
        if not dry_run:
            t_row = conn.execute("SELECT COUNT(*) FROM items WHERE bot_id = ?", (b.id,)).fetchone()
            total_items = t_row[0] if t_row else 0
            o_row = conn.execute("SELECT COUNT(*) FROM items WHERE bot_id = ? AND status = 'open'", (b.id,)).fetchone()
            open_items = o_row[0] if o_row else 0

        date_range = "-"
        if stats["min_date"] and stats["max_date"]:
            date_range = (
                stats["min_date"]
                if stats["min_date"] == stats["max_date"]
                else f"{stats['min_date']}..{stats['max_date']}"
            )

        summary_results.append({
            "slug": slug,
            "name": stats["name"],
            "created": stats["created"],
            "updated": stats["updated"],
            "unchanged": stats["unchanged"],
            "items_total": total_items,
            "items_open": open_items,
            "date_range": date_range,
        })

    # Print summary table
    print(f"\n{'='*78}")
    print(f"{'Bot':<18} | {'Created':<7} | {'Updated':<7} | {'Unchanged':<9} | {'Items (Open/Tot)':<16} | {'Dates':<15}")
    print(f"{'-'*78}")
    for res in summary_results:
        items_str = f"{res['items_open']}/{res['items_total']}"
        print(
            f"{res['name']:<18} | {res['created']:<7} | {res['updated']:<7} | {res['unchanged']:<9} | {items_str:<16} | {res['date_range']:<15}"
        )
    print(f"{'='*78}\n")

    return {"summary": summary_results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bot Briefs Importer")
    parser.add_argument("--since", help="Import briefs since date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="Dry run without writing to DB")
    args = parser.parse_args()

    since_date = None
    if args.since:
        since_date = datetime.strptime(args.since, "%Y-%m-%d").date()

    run_import(since=since_date, dry_run=args.dry_run)
