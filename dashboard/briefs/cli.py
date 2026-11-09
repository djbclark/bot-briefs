from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from briefs.actions import ack, inbox, list_actions, list_actions_for_item, mark
from briefs.config import (
    BRIEFS_PUBLIC_URL,
    BRIEFS_DB,
    BRIEFS_PORT,
    find_bot,
    today_et,
)
from briefs.db import connect, init_schema
from briefs.importer import run_import
from briefs.parse import clean_title, compute_item_key, extract_flags, filter_safe_links, parse_brief
from briefs.store import items_open, search, upsert_report


def _parse_links_cell(raw_links: Any) -> list[dict[str, str]]:
    """Parse links from raw cell content: string, HYPERLINK formula, list, or dict."""
    if not raw_links:
        return []

    def _parse_str(s: str) -> list[dict[str, str]]:
        s = s.strip()
        if not s:
            return []

        # Check for Excel/Sheets =HYPERLINK("url","label") formula
        formulas = list(re.finditer(
            r'=HYPERLINK\(\s*["\']([^"\']+)["\']\s*,\s*["\']([^"\']+)["\']\s*\)',
            s,
            re.IGNORECASE,
        ))
        if formulas:
            res: list[dict[str, str]] = []
            for m in formulas:
                res.append({"label": m.group(2).strip(), "url": m.group(1).strip()})
            return res

        # Multiple lines separated by newlines
        lines = [line.strip() for line in s.splitlines() if line.strip()]
        if len(lines) > 1:
            res = []
            for line in lines:
                res.extend(_parse_str(line))
            return res

        # Markdown link: [label](url)
        md_matches = list(re.finditer(r'\[([^\]]+)\]\(([^)]+)\)', s))
        if md_matches:
            return [{"label": m.group(1).strip(), "url": m.group(2).strip()} for m in md_matches]

        # Single line containing a URL
        url_match = re.search(r'https?://[^\s]+', s)
        if url_match:
            u = url_match.group(0).rstrip(".,;:)'\"")
            lbl = s[:url_match.start()].strip().rstrip(":- ")
            label = lbl if lbl else u
            return [{"label": label, "url": u}]

        return [{"label": s, "url": s}]

    if isinstance(raw_links, str):
        return _parse_str(raw_links)

    if isinstance(raw_links, dict):
        url = str(raw_links.get("url", ""))
        label = str(raw_links.get("label", url))
        return [{"label": label, "url": url}] if url else []

    if isinstance(raw_links, (list, tuple)):
        # Check if list is [label, url]
        if (
            len(raw_links) == 2
            and isinstance(raw_links[0], str)
            and isinstance(raw_links[1], str)
            and ("http://" in raw_links[1] or "https://" in raw_links[1])
        ):
            return [{"label": raw_links[0].strip(), "url": raw_links[1].strip()}]

        res = []
        for item in raw_links:
            if isinstance(item, str):
                res.extend(_parse_str(item))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                res.append({"label": str(item[0]).strip(), "url": str(item[1]).strip()})
            elif isinstance(item, dict):
                u = str(item.get("url", ""))
                l = str(item.get("label", u))
                if u:
                    res.append({"label": l, "url": u})
        return res

    return []


def _ensure_display_ids(items: list[dict[str, Any]]) -> None:
    """Rows without an ID get their position; duplicate IDs get a suffix (IDs must be unique per report)."""
    seen: set[str] = set()
    for n, it in enumerate(items, 1):
        disp = str(it.get("display_id") or "").strip() or str(n)
        base, k = disp, 2
        while disp in seen:
            disp = f"{base}-{k}"
            k += 1
        seen.add(disp)
        it["display_id"] = disp


def parse_items_json_data(rows_data: list[Any]) -> list[dict[str, Any]]:
    """Parse reply-sheet rows into items.

    Each row is either a list ``[ID, Item, Status/details, [links...], key?]`` or a dict with keys
    ``id, item, details, links`` and optional ``key, status, due, priority``. A link is a URL string,
    ``[label, url]`` or ``{"label", "url"}``. Give ``key`` (e.g. "gmail:<thread>", a source URL, or any
    stable slug) whenever you have one: it is what ties an item together across days.
    """
    items: list[dict[str, Any]] = []
    pos = 0
    for row in rows_data:
        if isinstance(row, dict):
            disp_id = str(row.get("id", "")).strip()
            item_title = str(row.get("item", row.get("title", ""))).strip()
            status_details = str(row.get("details", row.get("status_details", ""))).strip()
            raw_links = row.get("links") or []
            explicit_key = row.get("key")
            extra = {k: row.get(k) for k in ("status", "priority") if row.get(k) is not None}
            if row.get("due"):
                extra["due_at"] = str(row["due"])
        else:
            disp_id = str(row[0]).strip() if len(row) > 0 else ""
            item_title = str(row[1]).strip() if len(row) > 1 else ""
            status_details = str(row[2]).strip() if len(row) > 2 else ""
            raw_links = row[3] if len(row) > 3 and row[3] else []
            explicit_key = row[4] if len(row) > 4 else None
            extra = {}

        parent_disp_id = disp_id.rsplit(".", 1)[0] if "." in disp_id else None
        links = filter_safe_links(_parse_links_cell(raw_links))

        pos += 1
        flags = extract_flags(f"{item_title} {status_details}")
        title = clean_title(item_title)
        # Key from the item's own source links first (sheet links are ignored by compute_item_key).
        key_text = " ".join([item_title, status_details] + [lk["url"] for lk in links])
        item_key = str(explicit_key).strip() if explicit_key else compute_item_key(key_text, title)

        it: dict[str, Any] = {
            "display_id": disp_id,
            "parent_display_id": parent_disp_id,
            "position": pos,
            "section": None,
            "text_md": status_details,
            "title": title,
            "links": links,
            "flags": flags,
            "item_key": item_key,
        }
        it.update(extra)
        items.append(it)
    return items


def parse_sheet_rows_data(rows_data: list[Any]) -> list[dict[str, Any]]:
    """Parse raw reply-sheet rows into items.

    Accepts:
    - 5 cols: [ID, Item, Status/details, Your reply, Links]
    - 4 cols: [ID, Item, Status/details, Links]
    - Dict form: {"id", "item", "details", "links", "key"}
    Skips header row (row[0] case-insensitively 'id').
    Keys come from links / title hash. Optional trailing extra elements are ignored.
    """
    items: list[dict[str, Any]] = []
    pos = 0
    for row in rows_data:
        if isinstance(row, dict):
            disp_id = str(row.get("id", "")).strip()
            if disp_id.lower() == "id":
                continue
            item_title = str(row.get("item", row.get("title", ""))).strip()
            status_details = str(row.get("details", row.get("status_details", ""))).strip()
            raw_links = row.get("links") or []
            explicit_key = row.get("key")
            extra = {k: row.get(k) for k in ("status", "priority") if row.get(k) is not None}
            if row.get("due"):
                extra["due_at"] = str(row["due"])
        elif isinstance(row, (list, tuple)):
            if not row:
                continue
            disp_id = str(row[0]).strip()
            if disp_id.lower() == "id":
                # Skip header row
                continue
            item_title = str(row[1]).strip() if len(row) > 1 and row[1] is not None else ""
            status_details = str(row[2]).strip() if len(row) > 2 and row[2] is not None else ""
            if len(row) == 4:
                raw_links = row[3]
            elif len(row) >= 5:
                # 5-col: row[3] is 'Your reply' (ignored) and row[4] is Links
                raw_links = row[4]
            else:
                raw_links = []
            explicit_key = None
            extra = {}
        else:
            continue

        parent_disp_id = disp_id.rsplit(".", 1)[0] if "." in disp_id else None
        links = filter_safe_links(_parse_links_cell(raw_links))

        pos += 1
        flags = extract_flags(f"{item_title} {status_details}")
        title = clean_title(item_title)
        key_text = " ".join([item_title, status_details] + [lk["url"] for lk in links])
        item_key = str(explicit_key).strip() if explicit_key else compute_item_key(key_text, title)

        it: dict[str, Any] = {
            "display_id": disp_id,
            "parent_display_id": parent_disp_id,
            "position": pos,
            "section": None,
            "text_md": status_details,
            "title": title,
            "links": links,
            "flags": flags,
            "item_key": item_key,
        }
        it.update(extra)
        items.append(it)
    return items


def cmd_init(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    init_schema(conn)
    print(f"Database schema initialized at {args.db or BRIEFS_DB}")


def cmd_publish(args: argparse.Namespace) -> None:
    bot = find_bot(args.bot)
    if not bot:
        sys.stderr.write(f"Error: Unknown bot '{args.bot}'\n")
        sys.exit(1)

    rep_date = args.date or today_et()
    kind = args.kind or "daily"

    # Read body markdown from file or stdin
    body_md = ""
    file_path = getattr(args, "file", None)
    if file_path:
        if file_path == "-":
            try:
                body_md = sys.stdin.read()
            except (OSError, ValueError):
                body_md = ""
        else:
            with open(file_path, "r", encoding="utf-8") as f:
                body_md = f.read()
    elif not getattr(args, "items_json", None) and not getattr(args, "from_sheet_rows", None):
        try:
            if not sys.stdin.isatty():
                body_md = sys.stdin.read()
        except (OSError, ValueError):
            body_md = ""

    # Parse items json or sheet rows if supplied
    parsed_items = None
    if getattr(args, "items_json", None):
        with open(args.items_json, "r", encoding="utf-8") as f:
            rows_data = json.load(f)
            parsed_items = parse_items_json_data(rows_data)
    elif getattr(args, "from_sheet_rows", None):
        with open(args.from_sheet_rows, "r", encoding="utf-8") as f:
            rows_data = json.load(f)
            parsed_items = parse_sheet_rows_data(rows_data)
    if parsed_items:
        _ensure_display_ids(parsed_items)

    if parsed_items and not body_md:
        # Synthesize body markdown
        md_lines = [f"# {bot.name} Brief ({rep_date})\n"]
        for it in parsed_items:
            indent = "   - " if it["parent_display_id"] else ""
            md_lines.append(f"{indent}{it['display_id']}. **{it['title']}**: {it['text_md']}")
        body_md = "\n".join(md_lines)

    title = args.title or ""
    sheet_url = args.sheet_url or None

    conn = connect(args.db)
    init_schema(conn)

    report_id, status, n_items = upsert_report(
        conn,
        bot=bot,
        date=rep_date,
        kind=kind,
        title=title,
        body_md=body_md,
        sheet_url=sheet_url,
        source="cli",
        source_ref="brief-publish",
        items=parsed_items,
    )

    base = BRIEFS_PUBLIC_URL.rstrip("/")
    result = {
        "report_id": report_id,
        "status": status,
        "items": n_items,
        "url": f"{base}/r/{report_id}",
    }
    print(json.dumps(result))


def cmd_items(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    init_schema(conn)

    include_all = args.all
    items = items_open(conn, bot=args.bot, include_all=include_all)

    if args.json:
        print(json.dumps(items, indent=2))
        return

    # Human table
    print(f"\n{'ID':<6} | {'Bot':<12} | {'Status':<9} | {'First Seen':<10} | {'Last Seen':<10} | {'Title':<40} | {'Key'}")
    print(f"{'-'*110}")
    for it in items:
        disp = str(it.get("display_id") or "-")
        bot_name = str(it.get("bot_name") or "-")[:12]
        status = str(it.get("status") or "-")
        fs = str(it.get("first_seen") or "-")
        ls = str(it.get("last_seen") or "-")
        t = str(it.get("title") or "-")[:40]
        key = str(it.get("item_key") or "-")
        print(f"{disp:<6} | {bot_name:<12} | {status:<9} | {fs:<10} | {ls:<10} | {t:<40} | {key}")
    print(f"{'-'*110}\nTotal items: {len(items)}\n")


def _mirror_pull() -> None:
    """Dual-run with the VPS dashboard: fetch actions Daniel made there before reading. Silent, short, never fails."""
    try:
        from briefs.mirror import best_effort_pull
        best_effort_pull()
    except Exception:
        pass


def cmd_inbox(args: argparse.Namespace) -> None:
    _mirror_pull()
    bot = find_bot(args.bot)
    if not bot:
        sys.stderr.write(f"Error: Unknown bot '{args.bot}'\n")
        sys.exit(1)

    conn = connect(args.db)
    init_schema(conn)

    actions_list = inbox(
        conn,
        bot,
        include_seen=args.include_seen,
        mark_seen=(not args.peek),
    )

    if args.json:
        print(json.dumps(actions_list, indent=2))
        return

    if not actions_list:
        print(f"No pending actions for {bot.name}.")
        return

    for act in actions_list:
        act_id = act["id"]
        status = act["status"]
        act_type = act["type"]
        item = act.get("item", {})
        item_id = item.get("id", "-")
        disp_id = item.get("display_id") or "-"
        item_title = item.get("title") or "-"
        author = act.get("author") or "unknown"
        created = act.get("created_at") or "-"
        url = item.get("url") or ""

        print(f"Action #{act_id} [{status}] - {act_type}")
        print(f"  Item #{item_id} ({disp_id}): {item_title}")
        print(f"  Author: {author} at {created}")
        if act.get("payload"):
            print(f"  Payload: {json.dumps(act['payload'])}")
        if act.get("bot_note"):
            print(f"  Bot note: {act['bot_note']}")
        if url:
            print(f"  URL: {url}")
        print()


def cmd_mark(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    init_schema(conn)

    results = []
    for act_id in args.action_id:
        try:
            res = mark(conn, act_id, status=args.status)
            results.append(res)
        except ValueError as e:
            sys.stderr.write(f"Error: {e}\n")
            sys.exit(1)

    print(json.dumps(results, indent=2))


def cmd_ack(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    init_schema(conn)

    results = []
    for act_id in args.action_id:
        try:
            res = ack(conn, act_id, status=args.status, note=args.note, keep_open=args.keep_open)
            results.append(res)
        except ValueError as e:
            sys.stderr.write(f"Error: {e}\n")
            sys.exit(1)

    print(json.dumps(results, indent=2))


def cmd_actions(args: argparse.Namespace) -> None:
    _mirror_pull()
    conn = connect(args.db)
    init_schema(conn)

    if args.item is not None:
        it = conn.execute("SELECT id FROM items WHERE id = ?", (args.item,)).fetchone()
        if not it:
            sys.stderr.write(f"Error: Item not found: {args.item}\n")
            sys.exit(1)

        history = list_actions_for_item(conn, args.item)
        print(json.dumps(history, indent=2))
        return

    if args.status:
        acts = list_actions(conn, status=args.status, bot=args.bot)
        if args.json:
            print(json.dumps(acts, indent=2))
            return

        if not acts:
            print(f"No actions with status '{args.status}'.")
            return

        for a in acts:
            act_id = a["id"]
            st = a["status"]
            rank_str = f" [rank {a['rank']}]" if a.get("rank") is not None else ""
            item_info = a.get("item") or {}
            item_title = item_info.get("title") or "-"
            item_id = item_info.get("id") or "-"
            print(f"Action #{act_id}{rank_str} [{st}] - {a['type']} (item #{item_id}: {item_title})")
            if a.get("payload"):
                print(f"  Payload: {json.dumps(a['payload'])}")
            if a.get("bot_note"):
                print(f"  Bot note: {a['bot_note']}")
            print()


def cmd_import(args: argparse.Namespace) -> None:
    since_date = None
    if args.since:
        since_date = datetime.strptime(args.since, "%Y-%m-%d").date()
    run_import(since=since_date, dry_run=args.dry_run, db_path=args.db)


def cmd_search(args: argparse.Namespace) -> None:
    conn = connect(args.db)
    init_schema(conn)
    res = search(conn, args.query)

    print(f"\nSearch results for '{args.query}':")
    print(f"\n--- Reports ({len(res['reports'])}) ---")
    for r in res["reports"]:
        print(f"[{r['bot_name']}] {r['report_date']} ({r['kind']}): {r['title']} -> /r/{r['id']}")
        if r.get("body_snippet"):
            print(f"   Snippet: {r['body_snippet']}")

    print(f"\n--- Items ({len(res['items'])}) ---")
    for it in res["items"]:
        print(f"[{it['bot_name']}] ({it['status']}) {it['title']} -> /i/{it['id']}")
        if it.get("body_snippet"):
            print(f"   Snippet: {it['body_snippet']}")
    print()


def cmd_serve(args: argparse.Namespace) -> None:
    import uvicorn
    from briefs.web import create_app

    app = create_app(db_path=args.db)
    port = args.port or BRIEFS_PORT
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


def main() -> None:
    parser = argparse.ArgumentParser(prog="brief", description="Bot Briefs CLI")
    parser.add_argument("--db", help="Path to database file (default BRIEFS_DB)", default=None)
    subparsers = parser.add_subparsers(dest="command")

    # init
    p_init = subparsers.add_parser("init", help="Initialize database schema")
    p_init.set_defaults(func=cmd_init)

    # publish
    p_pub = subparsers.add_parser("publish", help="Publish a report directly to DB")
    p_pub.add_argument("--bot", required=True, help="Bot slug, name, UUID, or CLI name")
    p_pub.add_argument("--date", help="Report date (YYYY-MM-DD, default today ET)")
    p_pub.add_argument("--kind", choices=["daily", "weekly", "cos", "adhoc"], default="daily")
    p_pub.add_argument("--title", help="Report title")
    p_pub.add_argument("--sheet-url", help="Google Spreadsheet URL")
    p_pub.add_argument("--file", "--md", dest="file", help="Markdown brief file path (or stdin)")
    group = p_pub.add_mutually_exclusive_group()
    group.add_argument("--items-json", help="Path to JSON file with structured item rows")
    group.add_argument("--from-sheet-rows", help="Path to JSON file with raw sheet rows")
    p_pub.set_defaults(func=cmd_publish)

    # items
    p_items = subparsers.add_parser("items", help="List items")
    p_items.add_argument("--bot", help="Filter by bot slug/name/UUID")
    p_items.add_argument("--open", action="store_true", default=True, help="Show open items (default)")
    p_items.add_argument("--all", action="store_true", help="Show all statuses")
    p_items.add_argument("--json", action="store_true", help="Output as JSON")
    p_items.set_defaults(func=cmd_items)

    # inbox
    p_inbox = subparsers.add_parser("inbox", help="View pending actions for a bot")
    p_inbox.add_argument("--bot", required=True, help="Bot slug, name, UUID, or CLI name")
    p_inbox.add_argument("--json", action="store_true", help="Output as JSON")
    p_inbox.add_argument("--include-seen", action="store_true", help="Include seen actions")
    p_inbox.add_argument("--peek", action="store_true", help="Do not mark actions as seen")
    p_inbox.set_defaults(func=cmd_inbox)

    # mark
    p_mark = subparsers.add_parser("mark", help="Mark actions as processing")
    p_mark.add_argument("action_id", nargs="+", type=int, help="Action ID(s) to mark")
    p_mark.add_argument("status", choices=["processing"], help="Status to mark")
    p_mark.set_defaults(func=cmd_mark)

    # ack
    p_ack = subparsers.add_parser("ack", help="Acknowledge actions as applied, failed, or discuss")
    p_ack.add_argument("action_id", nargs="+", type=int, help="Action ID(s) to acknowledge")
    p_ack.add_argument("--status", required=True, choices=["applied", "failed", "discuss"], help="Action status")
    p_ack.add_argument("--note", help="Optional note from bot")
    p_ack.add_argument("--keep-open", action="store_true", help="Keep item open on applied")
    p_ack.set_defaults(func=cmd_ack)

    # actions
    p_actions = subparsers.add_parser("actions", help="List actions")
    g_actions = p_actions.add_mutually_exclusive_group(required=True)
    g_actions.add_argument("--item", type=int, help="Item ID")
    g_actions.add_argument("--status", help="Filter by action status (e.g. submitted, queued, etc.)")
    p_actions.add_argument("--bot", help="Filter by bot")
    p_actions.add_argument("--json", action="store_true", help="Output as JSON")
    p_actions.set_defaults(func=cmd_actions)

    # import
    p_imp = subparsers.add_parser("import", help="Run importer")
    p_imp.add_argument("--since", help="Import since date (YYYY-MM-DD)")
    p_imp.add_argument("--dry-run", action="store_true", help="Dry run without writing")
    p_imp.set_defaults(func=cmd_import)

    # search
    p_srch = subparsers.add_parser("search", help="Search reports and items")
    p_srch.add_argument("query", help="Search query")
    p_srch.set_defaults(func=cmd_search)

    # serve
    p_srv = subparsers.add_parser("serve", help="Run web server")
    p_srv.add_argument("--port", type=int, help="Port to bind (default 8642)")
    p_srv.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(1)

    try:
        args.func(args)
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
