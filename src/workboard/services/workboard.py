"""Use cases. The UI calls this class only; it calls the Repository only.

Every write is one local transaction. Checks run before the first write, so a
refused change leaves nothing behind. Saving locally never waits for the NAS:
a write only marks its snapshot as needing publication (sync_outbox).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
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
    clean_link,
    clean_text,
    history_hash,
    is_overdue,
    is_web_link,
    review_state,
    validate_due_date,
    validate_progress,
)
from workboard.domain.passwords import DUMMY_HASH, hash_password, validate_new_password, verify_password
from workboard.import_export.table import FIELDS as IMPORT_FIELDS
from workboard.import_export.table import normalize_due
from workboard.storage import Repository
from workboard.storage.database import APPEND_ONLY_TABLES

# Columns covered by each chain's hash; changing these invalidates existing chains.
_HASHED = {
    "progress_history": (
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
    ),
    "completion_review": (
        "id",
        "history_id",
        "project_id",
        "reviewer_id",
        "decision",
        "reason",
        "reviewed_at",
    ),
}
_NOT_STARTED = asdict(ProgressValues(Status.NOT_STARTED.value, 0))
_WEEK = timedelta(days=7)
NO_ACCOUNT = "no account with that ID"


def _is(row: dict, state: ReviewState) -> bool:
    return row["review_state"] == state


def _overdue(row: dict, today: str) -> bool:
    return is_overdue(row["due_date"], row["status"], today)


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

    @contextmanager
    def _write(self) -> Iterator[None]:
        # IMMEDIATE takes the write lock before the checks, so a second app window
        # on the same file cannot change what they read before the write lands.
        self._conn.execute("BEGIN IMMEDIATE")
        with self._conn:
            yield

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
        if not self._is_editor(project_id, actor):
            raise PermissionDenied("only a project editor may change definitions")

    def _is_editor(self, project_id: str, actor: str) -> bool:
        return isinstance(actor, str) and self._repo.is_editor(project_id, actor)

    def _in_project(self, table: str, record_id: str | None, project_id: str) -> None:
        if record_id is None:
            return
        record = self._get(table, record_id)
        if record["project_id"] != project_id:
            raise ValidationError(f"{table} belongs to another project")
        if record["deleted"]:
            raise ValidationError(f"{table} was deleted and cannot be used for new work")

    def _define(self, table: str, project_id: str, record_id: str, **values: object) -> str:
        self._repo.insert(table, id=record_id, project_id=project_id, **values)
        self._repo.mark_dirty("definition", project_id, "", self._now())
        return record_id

    # ----- accounts (who is at the keyboard)
    # shortcut: no lockout after repeated wrong passwords, add one if a shared PC makes guessing realistic.
    # Nobody can set another person's password, editors included: whoever could would be able to log in
    # as an assignee and write their progress, or take over an account by first adding it as a member.
    # A forgotten password means a new account. must_change is only ever set in files from the short time
    # resets existed; check_login refuses those accounts.

    def has_accounts(self) -> bool:
        return self._repo.account_count() > 0

    def create_account(self, user_id: str, password: str) -> None:
        """Sign up on this PC. The ID is what editors put on the member list."""
        user_id = clean_text(user_id, "user", required=True)
        password_hash = hash_password(validate_new_password(user_id, password))
        with self._write():
            if self._repo.get_account(user_id):
                raise ValidationError("that ID is already taken")
            # Signing up must not hand over an ID that projects already trust. The one exception is
            # the very first account on this PC, which claims the data of whoever set the file up.
            if self._repo.account_count() and self._repo.is_referenced(user_id):
                raise PermissionDenied("that ID is already used in a project")
            self._repo.insert(
                "account", user_id=user_id, password_hash=password_hash, must_change=0, created_at=self._now()
            )

    def has_account(self, user_id: str) -> bool:
        return isinstance(user_id, str) and self._repo.get_account(user_id.strip()) is not None

    def _require_account(self, user_id: str) -> None:
        """Once accounts exist, a project may only name people who have one (sign up first, then get added).

        Otherwise an editor could park an ID in a project and nobody could ever claim it.
        """
        if self._repo.account_count() and not self._repo.get_account(user_id):
            raise ValidationError(NO_ACCOUNT)

    def check_login(self, user_id: str, password: str) -> None:
        """Raise PermissionDenied unless the ID and password match.

        An unknown ID and a wrong password fail the same way and take the same time.
        """
        account = self._repo.get_account(user_id.strip()) if isinstance(user_id, str) else None
        matches = verify_password(password, account["password_hash"] if account else DUMMY_HASH)
        # must_change marks a password someone else set while resets briefly existed. That person
        # still knows it, so the account stays shut; its owner signs up again under a new ID.
        if not (account and matches) or account["must_change"]:
            raise PermissionDenied("wrong ID or password")

    def change_password(self, user_id: str, old_password: str, new_password: str) -> None:
        self.check_login(user_id, old_password)
        user_id = user_id.strip()
        if new_password == old_password:
            raise ValidationError("the new password must differ from the old one")
        password_hash = hash_password(validate_new_password(user_id, new_password))
        with self._write():
            self._repo.set_password(user_id, password_hash)

    # ----- definitions (project editors)

    def create_project(self, actor: str, name: str, description: str = "") -> str:
        actor = clean_text(actor, "actor", required=True)
        name = clean_text(name, "name", required=True)
        description = clean_text(description, "description")
        project_id = str(uuid4())
        with self._write():
            now = self._now()
            self._repo.insert(
                "project", id=project_id, name=name, description=description or "", created_at=now
            )
            self._repo.add_editor(project_id, actor)
            self._repo.add_member(project_id, actor)
            self._repo.mark_dirty("definition", project_id, "", now)
        return project_id

    def add_editor(self, actor: str, project_id: str, user_id: str) -> None:
        user_id = clean_text(user_id, "user", required=True)
        with self._write():
            self._require_editor(actor, project_id)
            self._require_account(user_id)
            self._repo.add_editor(project_id, user_id)
            self._repo.add_member(project_id, user_id)
            self._repo.mark_dirty("definition", project_id, "", self._now())

    def _item(self, kind: str, item_id: str) -> dict:
        if kind not in ("scope", "category"):
            raise ValidationError("unknown kind of item")
        return self._get(kind, item_id)

    def rename_item(self, actor: str, kind: str, item_id: str, name: str) -> None:
        """Rename a scope or category item; work that uses it shows the new name."""
        name = clean_text(name, "name", required=True)
        with self._write():
            item = self._item(kind, item_id)
            self._require_editor(actor, item["project_id"])
            self._repo.set_field(kind, item_id, "name", name)
            self._repo.mark_dirty("definition", item["project_id"], "", self._now())

    def delete_item(self, actor: str, kind: str, item_id: str) -> None:
        """Retire a scope or category item: it leaves the pick lists, old work keeps showing it."""
        with self._write():
            item = self._item(kind, item_id)
            self._require_editor(actor, item["project_id"])
            self._repo.set_field(kind, item_id, "deleted", 1)
            self._repo.mark_dirty("definition", item["project_id"], "", self._now())

    def remove_member(self, actor: str, project_id: str, user_id: str) -> None:
        """Take someone off the member list. Their assignments and progress stay as they are."""
        user_id = clean_text(user_id, "user", required=True)
        with self._write():
            self._require_editor(actor, project_id)
            if self._repo.is_editor(project_id, user_id):
                raise ValidationError("remove the editor role first")
            self._repo.remove_member(project_id, user_id)
            self._repo.mark_dirty("definition", project_id, "", self._now())

    def remove_editor(self, actor: str, project_id: str, user_id: str) -> None:
        """Take the editor role away; the person stays a member. A project keeps at least one editor."""
        user_id = clean_text(user_id, "user", required=True)
        with self._write():
            self._require_editor(actor, project_id)
            if not self._repo.is_editor(project_id, user_id):
                raise ValidationError("that member is not an editor")
            if self._repo.editor_count(project_id) == 1:
                raise ValidationError("a project needs at least one editor")
            self._repo.remove_editor(project_id, user_id)
            self._repo.mark_dirty("definition", project_id, "", self._now())

    # ----- import from a spreadsheet (project editors)

    def _plan_import(self, actor: str, project_id: str, rows: list[dict]) -> list[dict]:
        """Judge every sheet row without writing: new, duplicate, error (with a reason) or blank.

        A row is the same work as an existing one when version, field and assignee match and the
        reference ID matches (or, with no reference ID on the row, the title). Rows judged `new`
        carry the cleaned values to register under `data`.
        """
        self._require_editor(actor, project_id)
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValidationError("import rows must be a list of rows")

        def fold(value: object) -> str:
            return value.strip().casefold() if isinstance(value, str) else ""

        people: dict[str, set[str]] = {}
        for member in self._repo.members(project_id):
            for known in {member["user_id"], member["display_name"]}:
                if fold(known):
                    people.setdefault(fold(known), set()).add(member["user_id"])
        items: dict[str, dict[str, dict]] = {"scope": {}, "category": {}}
        names: dict[str, str] = {}
        for kind in items:
            # A live item wins over a retired one of the same name.
            for record in sorted(self._repo.listing(kind, project_id), key=lambda r: -r["deleted"]):
                items[kind][fold(record["name"])] = record
                names[record["id"]] = fold(record["name"])
        seen: set[tuple] = set()
        for existing in self._repo.assignment_rows(project_id=project_id):
            base = (names.get(existing["scope_id"]), names.get(existing["category_id"]), existing["assignee_id"])
            seen.add((*base, "title", fold(existing["title"])))
            if fold(existing["external_ref"]):
                seen.add((*base, "ref", fold(existing["external_ref"])))

        planned = []
        for row in rows:
            text = {field: row[field].strip() if isinstance(row.get(field), str) else "" for field in IMPORT_FIELDS}
            entry: dict = {"line": row.get("line"), "title": text["title"], "verdict": "new", "reason": None}
            planned.append(entry)
            if not text["title"]:
                entry["verdict"] = "blank"
                continue
            owners = people.get(fold(text["assignee"]), set())
            due_date = None
            if not text["scope"]:
                reason = "no_scope"
            elif not text["category"]:
                reason = "no_category"
            elif not text["assignee"]:
                reason = "no_assignee"
            elif not owners:
                reason = "unknown_assignee"
            elif len(owners) > 1:
                reason = "ambiguous_assignee"
            elif items["scope"].get(fold(text["scope"]), {}).get("deleted"):
                reason = "deleted_scope"
            elif items["category"].get(fold(text["category"]), {}).get("deleted"):
                reason = "deleted_category"
            elif text["external_url"] and not is_web_link(text["external_url"]):
                reason = "bad_link"
            else:
                reason = None
                try:
                    due_date = normalize_due(text["due_date"])
                except ValueError:
                    reason = "bad_due_date"
            if reason:
                entry.update(verdict="error", reason=reason)
                continue
            (assignee_id,) = owners
            base = (fold(text["scope"]), fold(text["category"]), assignee_id)
            by_title = (*base, "title", fold(text["title"]))
            key = (*base, "ref", fold(text["external_ref"])) if text["external_ref"] else by_title
            if key in seen:
                entry["verdict"] = "duplicate"
                continue
            seen.update((key, by_title))
            entry["data"] = {**text, "assignee_id": assignee_id, "due_date": due_date}
        return planned

    @staticmethod
    def _import_summary(planned: list[dict], known: dict[str, set[str]]) -> dict:
        counts = {"read": len(planned), "new": 0, "duplicate": 0, "error": 0, "blank": 0}
        fresh: dict[str, list[str]] = {"scope": [], "category": []}
        for entry in planned:
            counts[entry["verdict"]] += 1
            for kind in fresh if entry["verdict"] == "new" else ():
                name = entry["data"][kind]
                if name.casefold() not in known[kind]:
                    known[kind].add(name.casefold())
                    fresh[kind].append(name)
        return {
            "rows": [{key: value for key, value in entry.items() if key != "data"} for entry in planned],
            "counts": counts,
            "new_scopes": fresh["scope"],
            "new_categories": fresh["category"],
        }

    def _known_items(self, project_id: str) -> dict[str, set[str]]:
        return {
            kind: {record["name"].strip().casefold() for record in self._repo.listing(kind, project_id, live_only=True)}
            for kind in ("scope", "category")
        }

    def plan_import(self, actor: str, project_id: str, rows: list[dict]) -> dict:
        """Preview an import. Nothing is saved. The four verdict counts always add up to `read`."""
        planned = self._plan_import(actor, project_id, rows)
        return self._import_summary(planned, self._known_items(project_id))

    def apply_import(self, actor: str, project_id: str, rows: list[dict]) -> dict:
        """Register every row judged `new`, creating the version and field items they name.

        One transaction, judged again inside it, so the result matches what is in the database
        at that moment even if something changed after the preview.
        """
        with self._write():
            planned = self._plan_import(actor, project_id, rows)
            summary = self._import_summary(planned, self._known_items(project_id))
            now = self._now()
            ids: dict[str, dict[str, str]] = {
                kind: {r["name"].strip().casefold(): r["id"] for r in self._repo.listing(kind, project_id, live_only=True)}
                for kind in ("scope", "category")
            }
            for name in summary["new_scopes"]:
                ids["scope"][name.casefold()] = str(uuid4())
                self._repo.insert("scope", id=ids["scope"][name.casefold()], project_id=project_id, name=name, kind="")
            for name in summary["new_categories"]:
                ids["category"][name.casefold()] = str(uuid4())
                self._repo.insert(
                    "category",
                    id=ids["category"][name.casefold()],
                    project_id=project_id,
                    name=name,
                    position=len(ids["category"]),
                )
            for entry in planned:
                if entry["verdict"] != "new":
                    continue
                data = entry["data"]
                work_item_id = WorkItemId.new().value
                self._repo.insert(
                    "work_item",
                    id=work_item_id,
                    project_id=project_id,
                    title=data["title"],
                    scope_id=ids["scope"][data["scope"].casefold()],
                    category_id=ids["category"][data["category"].casefold()],
                    external_ref=data["external_ref"] or None,
                    external_url=data["external_url"] or None,
                    created_by=actor,
                )
                self._repo.insert(
                    "assignment",
                    id=str(uuid4()),
                    work_item_id=work_item_id,
                    assignee_id=data["assignee_id"],
                    due_date=data["due_date"],
                    created_at=now,
                )
            if summary["counts"]["new"]:
                self._repo.mark_dirty("definition", project_id, "", now)
            return summary

    def get_import_mapping(self, project_id: str) -> dict[str, str]:
        """Which sheet heading the editor matched to each field last time, on this PC."""
        self._get("project", project_id)
        try:
            saved = json.loads(self._repo.import_mapping(project_id) or "{}")
        except ValueError:
            return {}
        return {field: saved[field] for field in IMPORT_FIELDS if isinstance(saved.get(field), str)}

    def save_import_mapping(self, actor: str, project_id: str, mapping: dict[str, str]) -> None:
        if not isinstance(mapping, dict):
            raise ValidationError("mapping must be a dictionary")
        kept = {field: mapping[field] for field in IMPORT_FIELDS if isinstance(mapping.get(field), str)}
        with self._write():
            self._require_editor(actor, project_id)
            self._repo.save_import_mapping(project_id, json.dumps(kept, ensure_ascii=False))

    def set_labels(self, actor: str, project_id: str, scope_label: str, category_label: str) -> None:
        """Rename the project's two grouping fields, e.g. 코드 버전 and 검증 분야."""
        scope_label = clean_text(scope_label, "scope label", required=True)
        category_label = clean_text(category_label, "category label", required=True)
        with self._write():
            self._require_editor(actor, project_id)
            self._repo.set_field("project", project_id, "scope_label", scope_label)
            self._repo.set_field("project", project_id, "category_label", category_label)
            self._repo.mark_dirty("definition", project_id, "", self._now())

    def add_member(self, actor: str, project_id: str, user_id: str, display_name: str | None = None) -> None:
        """Members can be picked as assignees and may add work for themselves."""
        user_id = clean_text(user_id, "user", required=True)
        display_name = clean_text(display_name, "display name")
        with self._write():
            self._require_editor(actor, project_id)
            self._require_account(user_id)
            self._repo.add_member(project_id, user_id, display_name)
            self._repo.mark_dirty("definition", project_id, "", self._now())

    def add_work_items(
        self,
        actor: str,
        project_id: str,
        items: list[dict],
        assignee_id: str,
        due_date: str | None = None,
        scope_id: str | None = None,
        category_id: str | None = None,
    ) -> list[str]:
        """Add several work items, each with one assignment, in one transaction.

        An editor may assign them to anyone. A member who is not an editor may add work
        only for themselves; `created_by` keeps that visible. Both grouping fields are
        required. Returns the assignment IDs.
        """
        assignee_id = clean_text(assignee_id, "assignee", required=True)
        due_date = validate_due_date(due_date)
        if not isinstance(items, list) or not items or not all(isinstance(item, dict) for item in items):
            raise ValidationError("at least one work item is required")
        cleaned = []
        for number, item in enumerate(items, start=1):
            try:
                cleaned.append(
                    (
                        clean_text(item.get("title"), "title", required=True),
                        clean_text(item.get("external_ref"), "external reference"),
                        clean_link(item.get("external_url"), "external link"),
                    )
                )
            except ValidationError as error:
                error.row = number  # lets the screen say which line to fix
                raise
        if scope_id is None or category_id is None:
            raise ValidationError("both grouping fields are required for new work")
        with self._write():
            self._get("project", project_id)
            if not self._is_editor(project_id, actor):
                if not (isinstance(actor, str) and self._repo.is_member(project_id, actor)):
                    raise PermissionDenied("only project members may add work")
                if assignee_id != actor:
                    raise PermissionDenied("members may add work only for themselves")
            self._require_account(assignee_id)
            self._in_project("scope", scope_id, project_id)
            self._in_project("category", category_id, project_id)
            now = self._now()
            assignment_ids = []
            for title, external_ref, external_url in cleaned:
                work_item_id = WorkItemId.new().value
                self._repo.insert(
                    "work_item",
                    id=work_item_id,
                    project_id=project_id,
                    title=title,
                    scope_id=scope_id,
                    category_id=category_id,
                    external_ref=external_ref,
                    external_url=external_url,
                    created_by=actor,
                )
                assignment_ids.append(str(uuid4()))
                self._repo.insert(
                    "assignment",
                    id=assignment_ids[-1],
                    work_item_id=work_item_id,
                    assignee_id=assignee_id,
                    due_date=due_date,
                    created_at=now,
                )
            self._repo.add_member(project_id, assignee_id)
            self._repo.mark_dirty("definition", project_id, "", now)
            return assignment_ids

    def create_scope(
        self, actor: str, project_id: str, name: str, kind: str = "", parent_id: str | None = None
    ) -> str:
        name = clean_text(name, "name", required=True)
        kind = clean_text(kind, "kind")
        with self._write():
            self._require_editor(actor, project_id)
            self._in_project("scope", parent_id, project_id)
            return self._define("scope", project_id, str(uuid4()), name=name, kind=kind or "", parent_id=parent_id)

    def create_category(self, actor: str, project_id: str, name: str, position: int = 0) -> str:
        name = clean_text(name, "name", required=True)
        if type(position) is not int:
            raise ValidationError("position must be an integer")
        with self._write():
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
        with self._write():
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
                external_url=clean_link(external_url, "external link"),
                created_by=actor,
            )

    def delete_work_item(self, actor: str, work_item_id: str) -> None:
        with self._write():
            item = self._get("work_item", work_item_id)
            self._require_editor(actor, item["project_id"])
            self._repo.set_field("work_item", work_item_id, "deleted", 1)
            self._repo.mark_dirty("definition", item["project_id"], "", self._now())

    def assign(self, actor: str, work_item_id: str, assignee_id: str, due_date: str | None = None) -> str:
        assignee_id = clean_text(assignee_id, "assignee", required=True)
        due_date = validate_due_date(due_date)
        assignment_id = str(uuid4())
        with self._write():
            item = self._get("work_item", work_item_id)
            self._require_editor(actor, item["project_id"])
            if item["deleted"]:
                raise ValidationError("work item is deleted")
            self._require_account(assignee_id)
            now = self._now()
            self._repo.insert(
                "assignment",
                id=assignment_id,
                work_item_id=work_item_id,
                assignee_id=assignee_id,
                due_date=due_date,
                created_at=now,
            )
            self._repo.add_member(item["project_id"], assignee_id)
            self._repo.mark_dirty("definition", item["project_id"], "", now)
        return assignment_id

    def _change_assignment(self, actor: str, assignment_id: str, column: str, value: object) -> None:
        with self._write():
            assignment = self._assignment(assignment_id)
            self._require_editor(actor, assignment["project_id"])
            if column == "due_date" and (assignment["cancelled"] or assignment["deleted"]):
                raise ValidationError("assignment is cancelled or its work item is deleted")
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
        with self._write():
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
            self._append("progress_history", "author_id", fields)
            self._repo.mark_dirty("progress", project_id, actor, now)
            return True

    def verify_history(self, project_id: str, author_id: str) -> bool:
        """True when the author's history chain in the project is intact."""
        return self._chain_intact("progress_history", "author_id", project_id, author_id)

    def verify_reviews(self, project_id: str, reviewer_id: str) -> bool:
        """True when the reviewer's review chain in the project is intact."""
        return self._chain_intact("completion_review", "reviewer_id", project_id, reviewer_id)

    def _append(self, table: str, owner_column: str, fields: dict) -> None:
        """Insert one row at the end of its (project, owner) hash chain."""
        prev_hash = self._repo.last_hash(table, owner_column, fields["project_id"], fields[owner_column])
        self._repo.insert(table, **fields, prev_hash=prev_hash, hash=history_hash(prev_hash, fields))

    def _chain_intact(self, table: str, owner_column: str, project_id: str, owner_id: str) -> bool:
        prev_hash = ""
        for row in self._repo.chain(table, owner_column, project_id, owner_id):
            fields = {key: row[key] for key in _HASHED[table]}
            if row["prev_hash"] != prev_hash or row["hash"] != history_hash(prev_hash, fields):
                return False
            prev_hash = row["hash"]
        return True

    def check_integrity(self) -> dict:
        """Look for edits made to the database file outside the app. Run at startup.

        Catches edited or removed rows inside a chain, a current value that no longer
        matches its latest history row, and removed append-only triggers. It cannot catch
        a chain rebuilt from scratch or rows cut from the end of a chain; other PCs catch
        those once snapshots are shared.
        """
        broken = {
            table: [
                owner
                for owner in self._repo.chain_owners(table, column)
                if not self._chain_intact(table, column, owner["project_id"], owner["owner_id"])
            ]
            for table, column in (("progress_history", "author_id"), ("completion_review", "reviewer_id"))
        }
        current = {row["assignment_id"]: row for row in self._repo.listing("assignment_progress")}
        latest = {row["assignment_id"]: row for row in self._repo.latest_history_rows()}
        mismatches = sorted(
            assignment_id
            for assignment_id in current.keys() | latest.keys()
            if assignment_id not in current
            or assignment_id not in latest
            or current[assignment_id]["updated_at"] != latest[assignment_id]["recorded_at"]
            or any(current[assignment_id][key] != latest[assignment_id][key] for key in _NOT_STARTED)
        )
        protection_removed = self._repo.append_only_trigger_count() < 2 * len(APPEND_ONLY_TABLES)
        return {
            "ok": not (any(broken.values()) or mismatches or protection_removed),
            "broken_history_chains": broken["progress_history"],
            "broken_review_chains": broken["completion_review"],
            "progress_mismatches": mismatches,
            "protection_removed": protection_removed,
        }

    # ----- completion review (another project editor)

    def review_completion(
        self, actor: str, assignment_id: str, decision: str, reason: str | None = None
    ) -> None:
        """Confirm or reject the latest completion report. Does not touch the assignee's status."""
        with self._write():
            assignment = self._assignment(assignment_id)
            project_id = assignment["project_id"]
            if not self._is_editor(project_id, actor):
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
            self._append(
                "completion_review",
                "reviewer_id",
                {
                    "id": str(uuid4()),
                    "history_id": rows[0]["history_id"],
                    "project_id": project_id,
                    "reviewer_id": actor,
                    "decision": decision,
                    "reason": reason,
                    "reviewed_at": now,
                },
            )
            self._repo.mark_dirty("review", project_id, actor, now)

    def pending_sync_count(self) -> int:
        return self._repo.outbox_count()

    def is_editor(self, actor: str, project_id: str) -> bool:
        """For the UI to enable editor controls; the write methods still check for themselves."""
        return isinstance(project_id, str) and self._is_editor(project_id, actor)

    def is_member(self, actor: str, project_id: str) -> bool:
        return isinstance(project_id, str) and isinstance(actor, str) and self._repo.is_member(project_id, actor)

    def list_members(self, project_id: str) -> list[dict]:
        self._get("project", project_id)
        return [{**member, "is_editor": bool(member["is_editor"])} for member in self._repo.members(project_id)]

    # ----- reads (anyone)
    # shortcut: aggregates count rows in Python per call, move to SQL if a project passes ~10k assignments.

    def _rows(self, **filters: str | None) -> list[dict]:
        rows = self._repo.assignment_rows(**filters)
        for row in rows:
            row["review_state"] = review_state(row["status"], row["review_decision"]).value
        return rows

    def list_projects(self) -> list[dict]:
        return self._repo.listing("project")

    # A blank or unknown project is NotFound, so it can never widen a listing to every project.

    def list_scopes(self, project_id: str, include_deleted: bool = False) -> list[dict]:
        self._get("project", project_id)
        return self._repo.listing("scope", project_id, live_only=not include_deleted)

    def list_categories(self, project_id: str, include_deleted: bool = False) -> list[dict]:
        self._get("project", project_id)
        return self._repo.listing("category", project_id, "position, rowid", live_only=not include_deleted)

    def list_assignments(
        self,
        project_id: str,
        scope_id: str | None = None,
        category_id: str | None = None,
        assignee_id: str | None = None,
    ) -> list[dict]:
        self._get("project", project_id)
        return self._rows(
            project_id=project_id, scope_id=scope_id, category_id=category_id, assignee_id=assignee_id
        )

    def my_assignments(self, actor: str) -> list[dict]:
        return self._rows(assignee_id=clean_text(actor, "actor", required=True))

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
        self._get("project", project_id)
        return [
            row
            for row in self._rows(project_id=project_id)
            if _is(row, ReviewState.PENDING) and row["assignee_id"] != actor
        ]

    def group_summary(
        self,
        project_id: str,
        by: str,
        today: str,
        scope_id: str | None = None,
        category_id: str | None = None,
    ) -> list[dict]:
        """One line per assignee or category. average_progress is self-reported; trust confirmed."""
        if by not in ("assignee", "category"):
            raise ValidationError("group by assignee or category")
        self._get("project", project_id)
        today = _today(today)
        return [
            {
                "key": key,
                "count": len(rows),
                "average_progress": round(sum(r["progress"] for r in rows) / len(rows), 1),
                **_counts(rows, today),
            }
            for key, rows in _group(
                self._rows(project_id=project_id, scope_id=scope_id, category_id=category_id), f"{by}_id"
            ).items()
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
        if project_id is not None:
            self._get("project", project_id)
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
                    "self_added": sum(r["created_by"] == assignee_id for r in rows),
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
                    "weeks_left": (
                        round(remaining * period.weeks / pace["confirmed"], 1) if pace["confirmed"] else None
                    ),
                    "weekly": pace["weekly"],
                }
            )
        return result
