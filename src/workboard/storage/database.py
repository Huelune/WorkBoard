"""Per-PC SQLite file. It never goes on the NAS (see docs/storage-design.md)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

_APPEND_ONLY = """
CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table}
BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;
CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table}
BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END;
"""

APPEND_ONLY_TABLES = ("progress_history", "completion_review")

# One entry per schema version. Once a version has shipped, append new entries and never edit it.
MIGRATIONS = [
    """
CREATE TABLE project (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE project_editor (
    project_id TEXT NOT NULL REFERENCES project(id),
    user_id TEXT NOT NULL,
    PRIMARY KEY (project_id, user_id)
);
CREATE TABLE scope (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project(id),
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT '',
    parent_id TEXT REFERENCES scope(id)
);
CREATE TABLE category (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project(id),
    name TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE work_item (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project(id),
    scope_id TEXT REFERENCES scope(id),
    category_id TEXT REFERENCES category(id),
    title TEXT NOT NULL,
    external_ref TEXT,
    external_url TEXT,
    deleted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE assignment (
    id TEXT PRIMARY KEY,
    work_item_id TEXT NOT NULL REFERENCES work_item(id),
    assignee_id TEXT NOT NULL,
    due_date TEXT,
    cancelled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX assignment_work_item ON assignment(work_item_id);
CREATE TABLE assignment_progress (
    assignment_id TEXT PRIMARY KEY REFERENCES assignment(id),
    status TEXT NOT NULL,
    progress INTEGER NOT NULL,
    result TEXT,
    hold_reason TEXT,
    evidence_ref TEXT,
    evidence_note TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE progress_history (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    assignment_id TEXT NOT NULL REFERENCES assignment(id),
    project_id TEXT NOT NULL REFERENCES project(id),
    author_id TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    status TEXT NOT NULL,
    progress INTEGER NOT NULL,
    result TEXT,
    hold_reason TEXT,
    evidence_ref TEXT,
    evidence_note TEXT,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
CREATE INDEX progress_history_assignment ON progress_history(assignment_id);
CREATE INDEX progress_history_chain ON progress_history(project_id, author_id);
CREATE TABLE completion_review (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    history_id TEXT NOT NULL REFERENCES progress_history(id),
    project_id TEXT NOT NULL REFERENCES project(id),
    reviewer_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT,
    reviewed_at TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
CREATE INDEX completion_review_history ON completion_review(history_id);
CREATE INDEX completion_review_chain ON completion_review(project_id, reviewer_id);
CREATE TABLE sync_outbox (
    kind TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES project(id),
    author_id TEXT NOT NULL DEFAULT '',
    dirty_since TEXT NOT NULL,
    PRIMARY KEY (kind, project_id, author_id)
);
"""
    + "".join(_APPEND_ONLY.format(table=table) for table in APPEND_ONLY_TABLES),
    # 2: project members (who can be picked as an assignee, with a display name) and who added a work item.
    """
CREATE TABLE project_member (
    project_id TEXT NOT NULL REFERENCES project(id),
    user_id TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (project_id, user_id)
);
INSERT OR IGNORE INTO project_member (project_id, user_id)
    SELECT project_id, user_id FROM project_editor;
INSERT OR IGNORE INTO project_member (project_id, user_id)
    SELECT w.project_id, a.assignee_id FROM assignment a JOIN work_item w ON w.id = a.work_item_id;
ALTER TABLE work_item ADD COLUMN created_by TEXT;
""",
    # 3: each project names its two grouping fields (scope, category) in its own words.
    """
ALTER TABLE project ADD COLUMN scope_label TEXT NOT NULL DEFAULT '대상 버전';
ALTER TABLE project ADD COLUMN category_label TEXT NOT NULL DEFAULT '업무 분야';
""",
]


def open_database(path: str | Path) -> sqlite3.Connection:
    """Open the local database and bring its schema up to date."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Without this, INSERT OR REPLACE deletes a row without firing the append-only triggers.
    conn.execute("PRAGMA recursive_triggers = ON")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > len(MIGRATIONS):
        conn.close()
        raise RuntimeError("database was created by a newer WorkBoard; update the app")
    for number in range(version, len(MIGRATIONS)):
        try:
            conn.executescript(f"BEGIN;{MIGRATIONS[number]}PRAGMA user_version = {number + 1};COMMIT;")
        except sqlite3.Error:
            conn.rollback()
            conn.close()
            raise
    return conn
