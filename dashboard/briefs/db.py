from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

from briefs.config import BOTS, BRIEFS_DB


def connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Connect to SQLite with WAL mode, foreign keys enabled, and Row factory."""
    path_str = db_path or BRIEFS_DB
    path = Path(path_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path_str)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: Optional[sqlite3.Connection] = None, db_path: Optional[str] = None) -> sqlite3.Connection:
    """Initialize database schema idempotently and populate the bots table."""
    close_when_done = False
    if conn is None:
        conn = connect(db_path)
        close_when_done = False  # caller or context can use it

    with conn:
        # Schema tables
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS bots (
            id TEXT PRIMARY KEY,
            slug TEXT UNIQUE,
            name TEXT,
            sort INT,
            area_default TEXT
        );

        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id TEXT REFERENCES bots(id),
            kind TEXT CHECK (kind IN ('daily', 'weekly', 'cos', 'adhoc')),
            report_date TEXT,
            title TEXT,
            body_md TEXT,
            sheet_url TEXT,
            source TEXT,
            source_ref TEXT,
            content_hash TEXT,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(bot_id, report_date, kind)
        );

        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id TEXT REFERENCES bots(id),
            item_key TEXT,
            title TEXT,
            status TEXT CHECK (status IN ('open', 'done', 'snoozed', 'dismissed', 'resolved')) DEFAULT 'open',
            priority INT NULL,
            area TEXT NULL,
            due_at TEXT NULL,
            snooze_until TEXT NULL,
            first_seen TEXT,
            last_seen TEXT,
            links_json TEXT DEFAULT '[]',
            meta_json TEXT DEFAULT '{}',
            UNIQUE(bot_id, item_key)
        );

        CREATE TABLE IF NOT EXISTS report_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER REFERENCES reports(id) ON DELETE CASCADE,
            item_id INTEGER REFERENCES items(id),
            display_id TEXT,
            parent_display_id TEXT NULL,
            position INT,
            section TEXT NULL,
            body_md TEXT,
            flags TEXT,
            UNIQUE(report_id, display_id)
        );

        CREATE TABLE IF NOT EXISTS actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER REFERENCES items(id) NULL,
            report_id INTEGER REFERENCES reports(id) NULL,
            bot_id TEXT REFERENCES bots(id) NULL,
            type TEXT,
            payload_json TEXT,
            author TEXT,
            status TEXT DEFAULT 'pending' CHECK (status IN ('queued', 'submitted', 'processing', 'applied', 'discuss', 'failed', 'pending', 'seen')),
            bot_note TEXT,
            wake_requested INTEGER DEFAULT 0,
            wake_status TEXT DEFAULT '',
            seen_at TEXT,
            acked_at TEXT,
            created_at TEXT,
            updated_at TEXT,
            rank INTEGER NULL
        );

        CREATE INDEX IF NOT EXISTS idx_reports_bot_date ON reports(bot_id, report_date);
        CREATE INDEX IF NOT EXISTS idx_items_bot_key ON items(bot_id, item_key);
        CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
        CREATE INDEX IF NOT EXISTS idx_report_items_report ON report_items(report_id);
        CREATE INDEX IF NOT EXISTS idx_report_items_item ON report_items(item_id);
        """)

        # Check if actions table exists and if its CHECK constraint allows 'queued'
        act_row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'actions'").fetchone()
        if act_row and act_row["sql"]:
            sql_def = act_row["sql"]
            if "'queued'" not in sql_def:
                cur = conn.execute("PRAGMA table_info(actions)")
                existing_cols = [row["name"] for row in cur.fetchall()]
                conn.execute("""
                CREATE TABLE actions_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id INTEGER REFERENCES items(id) NULL,
                    report_id INTEGER REFERENCES reports(id) NULL,
                    bot_id TEXT REFERENCES bots(id) NULL,
                    type TEXT,
                    payload_json TEXT,
                    author TEXT,
                    status TEXT DEFAULT 'pending' CHECK (status IN ('queued', 'submitted', 'processing', 'applied', 'discuss', 'failed', 'pending', 'seen')),
                    bot_note TEXT,
                    wake_requested INTEGER DEFAULT 0,
                    wake_status TEXT DEFAULT '',
                    seen_at TEXT,
                    acked_at TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    rank INTEGER NULL
                );
                """)
                all_new_cols = [
                    "id", "item_id", "report_id", "bot_id", "type", "payload_json", "author",
                    "status", "bot_note", "wake_requested", "wake_status", "seen_at", "acked_at",
                    "created_at", "updated_at", "rank"
                ]
                cols_to_copy = [c for c in existing_cols if c in all_new_cols]
                cols_str = ", ".join(cols_to_copy)
                conn.execute(f"INSERT INTO actions_new ({cols_str}) SELECT {cols_str} FROM actions;")
                conn.execute("DROP TABLE actions;")
                conn.execute("ALTER TABLE actions_new RENAME TO actions;")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_actions_bot_status ON actions(bot_id, status);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_actions_status ON actions(status);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_actions_item_id ON actions(item_id);")

        # Ensure missing columns in actions exist for existing DBs
        cur = conn.execute("PRAGMA table_info(actions)")
        existing_cols = {row["name"] for row in cur.fetchall()}
        cols_to_add = [
            ("bot_id", "TEXT"),
            ("wake_requested", "INTEGER DEFAULT 0"),
            ("wake_status", "TEXT DEFAULT ''"),
            ("seen_at", "TEXT"),
            ("acked_at", "TEXT"),
            ("rank", "INTEGER NULL"),
        ]
        for col_name, col_def in cols_to_add:
            if col_name not in existing_cols:
                conn.execute(f"ALTER TABLE actions ADD COLUMN {col_name} {col_def}")

        conn.execute("CREATE INDEX IF NOT EXISTS idx_actions_bot_status ON actions(bot_id, status);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_actions_status ON actions(status);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_actions_item_id ON actions(item_id);")

        # FTS5 tables
        conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS reports_fts USING fts5(
            title,
            body_md,
            content='reports',
            content_rowid='id'
        );
        """)

        # Triggers to keep reports_fts in sync
        conn.executescript("""
        CREATE TRIGGER IF NOT EXISTS reports_ai AFTER INSERT ON reports BEGIN
            INSERT INTO reports_fts(rowid, title, body_md) VALUES (new.id, new.title, new.body_md);
        END;
        CREATE TRIGGER IF NOT EXISTS reports_ad AFTER DELETE ON reports BEGIN
            INSERT INTO reports_fts(reports_fts, rowid, title, body_md) VALUES ('delete', old.id, old.title, old.body_md);
        END;
        CREATE TRIGGER IF NOT EXISTS reports_au AFTER UPDATE ON reports BEGIN
            INSERT INTO reports_fts(reports_fts, rowid, title, body_md) VALUES ('delete', old.id, old.title, old.body_md);
            INSERT INTO reports_fts(rowid, title, body_md) VALUES (new.id, new.title, new.body_md);
        END;
        """)

        # Items FTS table
        conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
            title,
            body,
            item_id UNINDEXED
        );
        """)

        # Dedicated local-only TODO review tables (never mirrored or dispatched)
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS todo_review_items (
            task_key TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            source TEXT NOT NULL,
            source_status TEXT NOT NULL,
            created_at TEXT NULL,
            due_at TEXT NULL,
            proposed_owner TEXT NOT NULL,
            links_json TEXT NOT NULL DEFAULT '[]',
            provenance_json TEXT NOT NULL DEFAULT '[]',
            imported_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS todo_review_decisions (
            task_key TEXT PRIMARY KEY,
            decision TEXT NULL CHECK (decision IS NULL OR decision IN ('keep', 'done', 'drop', 'changed')),
            note TEXT NOT NULL DEFAULT '',
            assigned_owner TEXT NOT NULL,
            reviewer TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS todo_review_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_key TEXT NOT NULL,
            prev_decision TEXT NULL,
            new_decision TEXT NULL,
            prev_owner TEXT NULL,
            new_owner TEXT NULL,
            prev_note TEXT NULL,
            new_note TEXT NULL,
            reviewer TEXT NOT NULL,
            audit_label TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_todo_review_audit_key ON todo_review_audit(task_key, id DESC);
        """)

        # Seed bots
        for b in BOTS:
            conn.execute(
                """
                INSERT INTO bots (id, slug, name, sort, area_default)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    slug = excluded.slug,
                    name = excluded.name,
                    sort = excluded.sort,
                    area_default = excluded.area_default;
                """,
                (b.id, b.slug, b.name, b.sort, b.area_default),
            )

    return conn
