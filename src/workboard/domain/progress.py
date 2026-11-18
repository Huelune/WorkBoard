"""Progress, evidence and review rules.

The status and progress rules follow the stage 0 assumptions in
docs/implementation-plan.md (progress is 0-100 entered by the assignee).
They are assumptions until the real spreadsheet practice is confirmed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from workboard.domain.errors import ValidationError


class Status(StrEnum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    ON_HOLD = "on_hold"
    DONE = "done"


class Result(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "na"


class Decision(StrEnum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class ReviewState(StrEnum):
    NONE = "none"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ProgressValues:
    status: str
    progress: int
    result: str | None = None
    hold_reason: str | None = None
    evidence_ref: str | None = None
    evidence_note: str | None = None


def clean_text(value: object, label: str, *, required: bool = False) -> str | None:
    """Trim text; blank becomes None, or an error when the field is required."""
    if value is not None and not isinstance(value, str):
        raise ValidationError(f"{label} must be text")
    text = value.strip() if value else ""
    if required and not text:
        raise ValidationError(f"{label} is required")
    return text or None


def is_web_link(value: object) -> bool:
    """Only http(s) links are ever opened; anything else is treated as plain text."""
    return isinstance(value, str) and value.startswith(("http://", "https://"))


def clean_link(value: object, label: str) -> str | None:
    text = clean_text(value, label)
    if text and not is_web_link(text):
        raise ValidationError(f"{label} must start with http:// or https://")
    return text


def validate_progress(values: ProgressValues) -> ProgressValues:
    """Check the status rules and return cleaned values.

    Fields that do not belong to the status (evidence and result outside
    DONE, hold reason outside ON_HOLD) are cleared rather than rejected.
    """
    try:
        status = Status(values.status)
    except ValueError as exc:
        raise ValidationError("unknown status") from exc
    progress = values.progress
    if type(progress) is not int:
        raise ValidationError("progress must be an integer")

    if status is Status.NOT_STARTED:
        allowed = progress == 0
    elif status is Status.DONE:
        allowed = progress == 100
    else:
        allowed = 0 <= progress <= 99
    if not allowed:
        raise ValidationError("progress does not match the status")

    if status is Status.ON_HOLD:
        return ProgressValues(
            status.value, progress, hold_reason=clean_text(values.hold_reason, "hold reason", required=True)
        )
    if status is Status.DONE:
        result = values.result
        if result is not None:
            try:
                result = Result(result).value
            except ValueError as exc:
                raise ValidationError("unknown result") from exc
        return ProgressValues(
            status.value,
            progress,
            result=result,
            evidence_ref=clean_text(values.evidence_ref, "evidence reference", required=True),
            evidence_note=clean_text(values.evidence_note, "evidence note"),
        )
    return ProgressValues(status.value, progress)


def validate_due_date(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("due date must be YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ValidationError("due date must be YYYY-MM-DD")
    return value


def is_overdue(due_date: str | None, status: str, today: str) -> bool:
    """Past its due date and not reported done. Dates are YYYY-MM-DD strings."""
    return bool(due_date) and due_date < today and status != Status.DONE


def history_hash(prev_hash: str, fields: dict) -> str:
    """Chain hash of one history row; the JSON form is fixed by tests."""
    payload = json.dumps(fields, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256((prev_hash + payload).encode("utf-8")).hexdigest()


def review_state(status: str, decision: str | None) -> ReviewState:
    if status != Status.DONE:
        return ReviewState.NONE
    if decision is None:
        return ReviewState.PENDING
    return ReviewState(decision)
