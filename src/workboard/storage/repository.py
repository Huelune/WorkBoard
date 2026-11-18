"""All SQL lives here. Callers own the transaction (`with conn:`)."""

from __future__ import annotations

import sqlite3

_ASSIGNMENT_ROWS = """
SELECT a.id AS assignment_id, w.project_id, w.id AS work_item_id, w.title,
       w.scope_id, w.category_id, w.external_ref, w.external_url, w.created_by,
       a.assignee_id, a.due_date,
       COALESCE(p.status, 'not_started') AS status, COALESCE(p.progress, 0) AS progress,
       p.result, p.hold_reason, p.evidence_ref, p.evidence_note,
       h.recorded_at AS reported_at, h.id AS history_id,
       r.decision AS review_decision, r.reason AS review_reason, r.reviewed_at
FROM assignment a
JOIN work_item w ON w.id = a.work_item_id
LEFT JOIN assignment_progress p ON p.assignment_id = a.id
LEFT JOIN progress_history h
       ON h.seq = (SELECT MAX(seq) FROM progress_history WHERE assignment_id = a.id)
LEFT JOIN completion_review r
       ON r.seq = (SELECT MAX(seq) FROM completion_review WHERE history_id = h.id)
WHERE a.cancelled = 0 AND w.deleted = 0
"""

_REJECTED_ROWS = """
SELECT w.project_id, h.author_id, r.reviewed_at
FROM progress_history h
JOIN completion_review r
  ON r.seq = (SELECT MAX(seq) FROM completion_review WHERE history_id = h.id)
JOIN assignment a ON a.id = h.assignment_id
JOIN work_item w ON w.id = a.work_item_id
WHERE r.decision = 'rejected' AND a.cancelled = 0 AND w.deleted = 0
"""


class Repository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # Table, column and filter names below always come from code, never from input.

    def insert(self, table: str, **values: object) -> None:
        columns = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        self._conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(values.values()))

    def all(self, sql: str, params: tuple = ()) -> list[dict]:
        return [dict(row) for row in self._conn.execute(sql, params)]

    def one(self, sql: str, params: tuple = ()) -> dict | None:
        row = self._conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def get(self, table: str, record_id: str) -> dict | None:
        return self.one(f"SELECT * FROM {table} WHERE id = ?", (record_id,))

    def set_field(self, table: str, record_id: str, column: str, value: object) -> None:
        self._conn.execute(f"UPDATE {table} SET {column} = ? WHERE id = ?", (value, record_id))

    def listing(self, table: str, project_id: str | None = None, order: str = "rowid") -> list[dict]:
        if project_id is None:
            return self.all(f"SELECT * FROM {table} ORDER BY {order}")
        return self.all(f"SELECT * FROM {table} WHERE project_id = ? ORDER BY {order}", (project_id,))

    def is_editor(self, project_id: str, user_id: str) -> bool:
        return (
            self.one(
                "SELECT 1 FROM project_editor WHERE project_id = ? AND user_id = ?", (project_id, user_id)
            )
            is not None
        )

    def add_editor(self, project_id: str, user_id: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO project_editor (project_id, user_id) VALUES (?, ?)", (project_id, user_id)
        )

    def add_member(self, project_id: str, user_id: str, display_name: str | None = None) -> None:
        """Add a member, or update the display name of an existing one when a name is given."""
        self._conn.execute(
            "INSERT OR IGNORE INTO project_member (project_id, user_id) VALUES (?, ?)", (project_id, user_id)
        )
        if display_name:
            self._conn.execute(
                "UPDATE project_member SET display_name = ? WHERE project_id = ? AND user_id = ?",
                (display_name, project_id, user_id),
            )

    def is_member(self, project_id: str, user_id: str) -> bool:
        return (
            self.one(
                "SELECT 1 FROM project_member WHERE project_id = ? AND user_id = ?", (project_id, user_id)
            )
            is not None
        )

    def members(self, project_id: str) -> list[dict]:
        return self.all(
            "SELECT m.user_id, m.display_name, e.user_id IS NOT NULL AS is_editor"
            " FROM project_member m LEFT JOIN project_editor e"
            " ON e.project_id = m.project_id AND e.user_id = m.user_id"
            " WHERE m.project_id = ? ORDER BY m.rowid",
            (project_id,),
        )

    def get_assignment(self, assignment_id: str) -> dict | None:
        """One assignment with its project and soft-delete flags, even when cancelled."""
        return self.one(
            "SELECT a.*, w.project_id, w.deleted FROM assignment a"
            " JOIN work_item w ON w.id = a.work_item_id WHERE a.id = ?",
            (assignment_id,),
        )

    def get_progress(self, assignment_id: str) -> dict | None:
        return self.one("SELECT * FROM assignment_progress WHERE assignment_id = ?", (assignment_id,))

    def save_progress(self, assignment_id: str, updated_at: str, **values: object) -> None:
        self._conn.execute("DELETE FROM assignment_progress WHERE assignment_id = ?", (assignment_id,))
        self.insert("assignment_progress", assignment_id=assignment_id, updated_at=updated_at, **values)

    # Hash chains: one per (project, owner) in progress_history (owner column author_id)
    # and in completion_review (owner column reviewer_id).

    def last_hash(self, table: str, owner_column: str, project_id: str, owner_id: str) -> str:
        row = self.one(
            f"SELECT hash FROM {table} WHERE project_id = ? AND {owner_column} = ? ORDER BY seq DESC LIMIT 1",
            (project_id, owner_id),
        )
        return row["hash"] if row else ""

    def chain(self, table: str, owner_column: str, project_id: str, owner_id: str) -> list[dict]:
        return self.all(
            f"SELECT * FROM {table} WHERE project_id = ? AND {owner_column} = ? ORDER BY seq",
            (project_id, owner_id),
        )

    def chain_owners(self, table: str, owner_column: str) -> list[dict]:
        return self.all(
            f"SELECT DISTINCT project_id, {owner_column} AS owner_id FROM {table} ORDER BY project_id, owner_id"
        )

    def latest_history_rows(self) -> list[dict]:
        return self.all(
            "SELECT h.* FROM progress_history h WHERE h.seq ="
            " (SELECT MAX(seq) FROM progress_history WHERE assignment_id = h.assignment_id)"
        )

    def append_only_trigger_count(self) -> int:
        return self._conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger'"
            " AND (name LIKE '%\\_no\\_update' ESCAPE '\\' OR name LIKE '%\\_no\\_delete' ESCAPE '\\')"
        ).fetchone()[0]

    def history(self, assignment_id: str) -> list[dict]:
        return self.all("SELECT * FROM progress_history WHERE assignment_id = ? ORDER BY seq", (assignment_id,))

    def reviews(self, assignment_id: str) -> list[dict]:
        return self.all(
            "SELECT r.* FROM completion_review r JOIN progress_history h ON h.id = r.history_id"
            " WHERE h.assignment_id = ? ORDER BY r.seq",
            (assignment_id,),
        )

    def mark_dirty(self, kind: str, project_id: str, author_id: str, now: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO sync_outbox (kind, project_id, author_id, dirty_since) VALUES (?, ?, ?, ?)",
            (kind, project_id, author_id, now),
        )

    def outbox_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0]

    def assignment_rows(self, **filters: str | None) -> list[dict]:
        """Live assignment rows; filters: project_id, assignee_id, scope_id, category_id, assignment_id."""
        columns = {
            "project_id": "w.project_id",
            "assignee_id": "a.assignee_id",
            "scope_id": "w.scope_id",
            "category_id": "w.category_id",
            "assignment_id": "a.id",
        }
        sql, params = _ASSIGNMENT_ROWS, []
        for name, value in filters.items():
            if value is not None:
                sql += f" AND {columns[name]} = ?"
                params.append(value)
        return self.all(sql + " ORDER BY a.rowid", tuple(params))

    def rejected_rows(self, project_id: str | None = None) -> list[dict]:
        """History rows whose effective (latest) review is a rejection."""
        if project_id is None:
            return self.all(_REJECTED_ROWS)
        return self.all(_REJECTED_ROWS + " AND w.project_id = ?", (project_id,))
