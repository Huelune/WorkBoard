"""Use cases. The UI calls this class only; it calls the Repository only.

Every write is one local transaction. Checks run before the first write, so a
refused change leaves nothing behind. Saving locally never waits for the NAS:
a write only marks its snapshot as needing publication (sync_outbox).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from math import ceil
from uuid import uuid4

from workboard.domain import (
    Decision,
    NotFound,
    PermissionDenied,
    ProgressValues,
    ReviewState,
    Status,
    ValidationError,
    WorkItemId,
    clean_text,
    history_hash,
    review_state,
    validate_due_date,
    validate_progress,
)
from workboard.storage import Repository

_HASHED = (
    "id",
    "assignment_id",
    "project_id",
    "author_id",
    "recorded_at",
    "status",
    "progress",
    "result",
    "hold_reason",
    "evidence_ref",
    "evidence_note",
)
_NOT_STARTED = asdict(ProgressValues(Status.NOT_STARTED.value, 0))
_WEEK = timedelta(days=7)


def _is(row: dict, state: ReviewState) -> bool:
    return row["review_state"] == state


def _overdue(row: dict, today: str) -> bool:
    return bool(row["due_date"]) and row["due_date"] < today and row["status"] != Status.DONE


def _counts(rows: list[dict], today: str) -> dict:
    return {
        "confirmed": sum(_is(r, ReviewState.CONFIRMED) for r in rows),
        "pending": sum(_is(r, ReviewState.PENDING) for r in rows),
        "rejected": sum(_is(r, ReviewState.REJECTED) for r in rows),
        "overdue": sum(_overdue(r, today) for r in rows),
    }


def _group(rows: list[dict], key: str) -> dict[object, list[dict]]:
    groups: dict[object, list[dict]] = {}
    for row in rows:
        groups.setdefault(row[key], []).append(row)
    return groups


def _today(value: str) -> str:
    if value is None:
        raise ValidationError("today is required")
    return validate_due_date(value)


class _Period:
    """Half-open UTC period [start, end) cut into 7-day buckets from start."""

    def __init__(self, start: datetime, end: datetime) -> None:
        for value in (start, end):
            if not isinstance(value, datetime) or value.tzinfo is None:
                raise ValidationError("period bounds must be timezone-aware datetimes")
        if end <= start:
            raise ValidationError("period end must be after its start")
        self.start, self.end = start, end
        self.weeks = (end - start) / _WEEK
        self.buckets = ceil(self.weeks)

    def bucket(self, stamp: str | None) -> int | None:
        """Bucket index of an ISO timestamp, or None when it is outside the period."""
        if stamp is None:
            return None
        moment = datetime.fromisoformat(stamp)
        return (moment - self.start) // _WEEK if self.start <= moment < self.end else None

    def pace(self, rows: list[dict]) -> dict:
        """Completion counts by report time; confirmation is the current review state."""
        weekly = [0] * self.buckets
        reported = 0
        for row in rows:
            index = self.bucket(row["reported_at"]) if row["status"] == Status.DONE else None
            if index is not None:
                reported += 1
                weekly[index] += _is(row, ReviewState.CONFIRMED)
        confirmed = sum(weekly)
        return {
            "reported": reported,
            "confirmed": confirmed,
            "per_week": round(confirmed / self.weeks, 2),
            "weekly": weekly,
        }


class WorkBoardService:
    def __init__(self, conn: sqlite3.Connection, clock: Callable[[], datetime] | None = None) -> None:
        self._conn = conn
        self._repo = Repository(conn)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # ----- helpers

    def _now(self) -> str:
        # Stamped here, never taken from the caller.
        return self._clock().astimezone(timezone.utc).isoformat(timespec="microseconds")

    def _get(self, table: str, record_id: str) -> dict:
        row = self._repo.get(table, record_id) if isinstance(record_id, str) else None
        if row is None:
            raise NotFound(f"{table} not found")
        return row

    def _assignment(self, assignment_id: str) -> dict:
        row = self._repo.get_assignment(assignment_id) if isinstance(assignment_id, str) else None
        if row is None:
            raise NotFound("assignment not found")
        return row

    def _require_editor(self, actor: str, project_id: str) -> None:
        self._get("project", project_id)
        if not self._repo.is_editor(project_id, actor):
            raise PermissionDenied("only a project editor may change definitions")

    def _in_project(self, table: str, record_id: str | None, project_id: str) -> None:
        if record_id is not None and self._get(table, record_id)["project_id"] != project_id:
            raise ValidationError(f"{table} belongs to another project")

    def _define(self, table: str, project_id: str, record_id: str, **values: object) -> str:
        self._repo.insert(table, id=record_id, project_id=project_id, **values)
        self._repo.mark_dirty("definition", project_id, "", self._now())
        return record_id

    # ----- definitions (project editors)

    def create_project(self, actor: str, name: str, description: str = "") -> str:
        actor = clean_text(actor, "actor", required=True)
        name = clean_text(name, "name", required=True)
        project_id = str(uuid4())
        with self._conn:
            now = self._now()
            self._repo.insert(
                "project", id=project_id, name=name, description=description or "", created_at=now
            )
            self._repo.add_editor(project_id, actor)
            self._repo.mark_dirty("definition", project_id, "", now)
        return project_id

    def add_editor(self, actor: str, project_id: str, user_id: str) -> None:
        user_id = clean_text(user_id, "user", required=True)
        with self._conn:
            self._require_editor(actor, project_id)
            self._repo.add_editor(project_id, user_id)
            self._repo.mark_dirty("definition", project_id, "", self._now())

    def create_scope(
        self, actor: str, project_id: str, name: str, kind: str = "", parent_id: str | None = None
    ) -> str:
        name = clean_text(name, "name", required=True)
        with self._conn:
            self._require_editor(actor, project_id)
            self._in_project("scope", parent_id, project_id)
            return self._define("scope", project_id, str(uuid4()), name=name, kind=kind or "", parent_id=parent_id)

    def create_category(self, actor: str, project_id: str, name: str, position: int = 0) -> str:
        name = clean_text(name, "name", required=True)
        with self._conn:
            self._require_editor(actor, project_id)
            return self._define("category", project_id, str(uuid4()), name=name, position=position)

    def create_work_item(
        self,
        actor: str,
        project_id: str,
        title: str,
        scope_id: str | None = None,
        category_id: str | None = None,
        external_ref: str | None = None,
        external_url: str | None = None,
    ) -> str:
        title = clean_text(title, "title", required=True)
        with self._conn:
            self._require_editor(actor, project_id)
            self._in_project("scope", scope_id, project_id)
            self._in_project("category", category_id, project_id)
            return self._define(
                "work_item",
                project_id,
                WorkItemId.new().value,
                title=title,
                scope_id=scope_id,
                category_id=category_id,
                external_ref=clean_text(external_ref, "external reference"),
                external_url=clean_text(external_url, "external link"),
            )

    def delete_work_item(self, actor: str, work_item_id: str) -> None:
        with self._conn:
            item = self._get("work_item", work_item_id)
            self._require_editor(actor, item["project_id"])
            self._repo.set_field("work_item", work_item_id, "deleted", 1)
            self._repo.mark_dirty("definition", item["project_id"], "", self._now())

    def assign(self, actor: str, work_item_id: str, assignee_id: str, due_date: str | None = None) -> str:
        assignee_id = clean_text(assignee_id, "assignee", required=True)
        due_date = validate_due_date(due_date)
        assignment_id = str(uuid4())
        with self._conn:
            item = self._get("work_item", work_item_id)
            self._require_editor(actor, item["project_id"])
            if item["deleted"]:
                raise ValidationError("work item is deleted")
            now = self._now()
            self._repo.insert(
                "assignment",
                id=assignment_id,
                work_item_id=work_item_id,
                assignee_id=assignee_id,
                due_date=due_date,
                created_at=now,
            )
            self._repo.mark_dirty("definition", item["project_id"], "", now)
        return assignment_id

    def _change_assignment(self, actor: str, assignment_id: str, column: str, value: object) -> None:
        with self._conn:
            assignment = self._assignment(assignment_id)
            self._require_editor(actor, assignment["project_id"])
            self._repo.set_field("assignment", assignment_id, column, value)
            self._repo.mark_dirty("definition", assignment["project_id"], "", self._now())

    def set_due_date(self, actor: str, assignment_id: str, due_date: str | None) -> None:
        self._change_assignment(actor, assignment_id, "due_date", validate_due_date(due_date))

    def cancel_assignment(self, actor: str, assignment_id: str) -> None:
        """Reassigning means cancelling and assigning anew; progress does not carry over."""
        self._change_assignment(actor, assignment_id, "cancelled", 1)

    # ----- progress (the assignee only)

    def update_progress(
        self,
        actor: str,
        assignment_id: str,
        status: str,
        progress: int,
        result: str | None = None,
        hold_reason: str | None = None,
        evidence_ref: str | None = None,
        evidence_note: str | None = None,
    ) -> bool:
        """Save the assignee's progress. Returns False when nothing changed."""
        with self._conn:
            assignment = self._assignment(assignment_id)
            if assignment["assignee_id"] != actor:
                raise PermissionDenied("only the assignee may change progress")
            if assignment["cancelled"] or assignment["deleted"]:
                raise ValidationError("assignment is cancelled or its work item is deleted")
            values = asdict(
                validate_progress(
                    ProgressValues(status, progress, result, hold_reason, evidence_ref, evidence_note)
                )
            )
            current = self._repo.get_progress(assignment_id)
            if values == ({key: current[key] for key in values} if current else _NOT_STARTED):
                return False
            now = self._now()
            project_id = assignment["project_id"]
            self._repo.save_progress(assignment_id, now, **values)
            fields = {
                "id": str(uuid4()),
                "assignment_id": assignment_id,
                "project_id": project_id,
                "author_id": actor,
                "recorded_at": now,
                **values,
            }
            prev_hash = self._repo.last_hash(project_id, actor)
            self._repo.insert(
                "progress_history", **fields, prev_hash=prev_hash, hash=history_hash(prev_hash, fields)
            )
            self._repo.mark_dirty("progress", project_id, actor, now)
            return True

    def verify_history(self, project_id: str, author_id: str) -> bool:
        """True when the author's history chain in the project is intact."""
        prev_hash = ""
        for row in self._repo.chain(project_id, author_id):
            fields = {key: row[key] for key in _HASHED}
            if row["prev_hash"] != prev_hash or row["hash"] != history_hash(prev_hash, fields):
                return False
            prev_hash = row["hash"]
        return True

    # ----- completion review (another project editor)

    def review_completion(
        self, actor: str, assignment_id: str, decision: str, reason: str | None = None
    ) -> None:
        """Confirm or reject the latest completion report. Does not touch the assignee's status."""
        with self._conn:
            assignment = self._assignment(assignment_id)
            project_id = assignment["project_id"]
            if not self._repo.is_editor(project_id, actor):
                raise PermissionDenied("only a project editor may review a completion")
            if assignment["assignee_id"] == actor:
                raise PermissionDenied("you may not review your own work")
            try:
                decision = Decision(decision).value
            except ValueError as exc:
                raise ValidationError("unknown decision") from exc
            reason = clean_text(reason, "reason", required=decision == Decision.REJECTED)
            rows = self._repo.assignment_rows(assignment_id=assignment_id)
            if not rows or rows[0]["status"] != Status.DONE:
                raise ValidationError("only a completed assignment can be reviewed")
            now = self._now()
            self._repo.insert(
                "completion_review",
                id=str(uuid4()),
                history_id=rows[0]["history_id"],
                reviewer_id=actor,
                decision=decision,
                reason=reason,
                reviewed_at=now,
            )
            self._repo.mark_dirty("review", project_id, actor, now)

    def pending_sync_count(self) -> int:
        return self._repo.outbox_count()

    # ----- reads (anyone)
    # shortcut: aggregates count rows in Python per call, move to SQL if a project passes ~10k assignments.

    def _rows(self, **filters: str | None) -> list[dict]:
        rows = self._repo.assignment_rows(**filters)
        for row in rows:
            row["review_state"] = review_state(row["status"], row["review_decision"]).value
        return rows

    def list_projects(self) -> list[dict]:
        return self._repo.listing("project")

    def list_scopes(self, project_id: str) -> list[dict]:
        return self._repo.listing("scope", project_id)

    def list_categories(self, project_id: str) -> list[dict]:
        return self._repo.listing("category", project_id, "position, rowid")

    def list_assignments(
        self,
        project_id: str,
        scope_id: str | None = None,
        category_id: str | None = None,
        assignee_id: str | None = None,
    ) -> list[dict]:
        return self._rows(
            project_id=project_id, scope_id=scope_id, category_id=category_id, assignee_id=assignee_id
        )

    def my_assignments(self, actor: str) -> list[dict]:
        return self._rows(assignee_id=actor)

    def assignment_detail(self, assignment_id: str) -> dict:
        rows = self._rows(assignment_id=assignment_id) if isinstance(assignment_id, str) else []
        if not rows:
            raise NotFound("assignment not found")
        return {
            "row": rows[0],
            "history": self._repo.history(assignment_id),
            "reviews": self._repo.reviews(assignment_id),
        }

    def pending_reviews(self, actor: str, project_id: str) -> list[dict]:
        """Completion reports waiting for review, without the actor's own work."""
        return [
            row
            for row in self._rows(project_id=project_id)
            if _is(row, ReviewState.PENDING) and row["assignee_id"] != actor
        ]

    def group_summary(self, project_id: str, by: str, today: str) -> list[dict]:
        """One line per assignee or category. average_progress is self-reported; trust confirmed."""
        if by not in ("assignee", "category"):
            raise ValidationError("group by assignee or category")
        today = _today(today)
        return [
            {
                "key": key,
                "count": len(rows),
                "average_progress": round(sum(r["progress"] for r in rows) / len(rows), 1),
                **_counts(rows, today),
            }
            for key, rows in _group(self._rows(project_id=project_id), f"{by}_id").items()
        ]

    def project_status(self, project_id: str, today: str) -> dict:
        self._get("project", project_id)
        today = _today(today)
        rows = self._rows(project_id=project_id)
        counts = _counts(rows, today)
        return {
            "total": len(rows),
            "confirmed": counts["confirmed"],
            "pending": counts["pending"],
            "rejected": counts["rejected"],
            "on_hold": sum(r["status"] == Status.ON_HOLD for r in rows),
            "overdue": counts["overdue"],
            "by_category": {key: len(group) for key, group in _group(rows, "category_id").items()},
        }

    def assignee_pace(self, start: datetime, end: datetime, project_id: str | None = None) -> list[dict]:
        """Per-assignee pace in one project, or across every project in this local database."""
        period = _Period(start, end)
        rejected: dict[str, int] = {}
        for row in self._repo.rejected_rows(project_id):
            if period.bucket(row["reviewed_at"]) is not None:
                rejected[row["author_id"]] = rejected.get(row["author_id"], 0) + 1
        result = []
        for assignee_id, rows in _group(self._rows(project_id=project_id), "assignee_id").items():
            pace = period.pace(rows)
            result.append(
                {
                    "assignee_id": assignee_id,
                    "assigned": len(rows),
                    "reported": pace["reported"],
                    "confirmed": pace["confirmed"],
                    "rejected": rejected.get(assignee_id, 0),
                    "per_week": pace["per_week"],
                    "weekly": pace["weekly"],
                }
            )
        return result

    def project_pace(self, start: datetime, end: datetime) -> list[dict]:
        """Per-project pace. weeks_left is remaining / per_week: a rough guess blind to task size."""
        period = _Period(start, end)
        result = []
        for project in self._repo.listing("project"):
            rows = self._rows(project_id=project["id"])
            pace = period.pace(rows)
            confirmed_total = sum(_is(r, ReviewState.CONFIRMED) for r in rows)
            remaining = len(rows) - confirmed_total
            result.append(
                {
                    "project_id": project["id"],
                    "name": project["name"],
                    "total": len(rows),
                    "confirmed_total": confirmed_total,
                    "remaining": remaining,
                    "confirmed": pace["confirmed"],
                    "per_week": pace["per_week"],
                    "weeks_left": round(remaining / pace["per_week"], 1) if pace["per_week"] else None,
                    "weekly": pace["weekly"],
                }
            )
        return result
