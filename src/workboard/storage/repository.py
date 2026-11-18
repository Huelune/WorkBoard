"""All SQL lives here. Callers own the transaction (`with conn:`)."""

from __future__ import annotations

import sqlite3

_ASSIGNMENT_ROWS = """
SELECT a.id AS assignment_id, w.project_id, w.id AS work_item_id, w.title,
       w.scope_id, w.category_id, w.external_ref, w.external_url,
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

    def last_hash(self, project_id: str, author_id: str) -> str:
        row = self.one(
            "SELECT hash FROM progress_history WHERE project_id = ? AND author_id = ?"
            " ORDER BY seq DESC LIMIT 1",
            (project_id, author_id),
        )
        return row["hash"] if row else ""

    def chain(self, project_id: str, author_id: str) -> list[dict]:
        return self.all(
            "SELECT * FROM progress_history WHERE project_id = ? AND author_id = ? ORDER BY seq",
            (project_id, author_id),
        )

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
