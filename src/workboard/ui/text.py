"""Korean labels and small text helpers. No Qt here, so it is testable on its own."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from html import escape

from workboard.domain import NotFound, PermissionDenied, ValidationError, is_overdue

STATUS = {"not_started": "미시작", "in_progress": "진행 중", "on_hold": "보류", "done": "완료"}
REVIEW = {"pending": "확인 대기", "confirmed": "확인됨", "rejected": "반려됨"}
RESULT = {"pass": "통과", "fail": "실패", "na": "해당 없음"}
DECISION = {"confirmed": "확인", "rejected": "반려"}
_BLOCKS = "▁▂▃▄▅▆▇█"


def state_label(row: dict) -> str:
    return REVIEW.get(row["review_state"]) or STATUS[row["status"]]


def attention(row: dict, today: str) -> int:
    """Sort rank: 0 rejected, 1 overdue, 2 everything else."""
    if row["review_state"] == "rejected":
        return 0
    return 1 if is_overdue(row["due_date"], row["status"], today) else 2


def group_notes(summary: dict) -> str:
    notes = [
        f"{label} {summary[key]}"
        for key, label in (("pending", "확인 대기"), ("rejected", "반려"), ("overdue", "기한 초과"))
        if summary[key]
    ]
    return " · ".join(notes) or "문제 없음"


def spark(values: list[int]) -> str:
    """One block character per week, scaled to the busiest week."""
    top = max(values, default=0)
    return "".join(_BLOCKS[(value * 8 - 1) // top] if value else "·" for value in values)


def local_time(stamp: str | None) -> str:
    return datetime.fromisoformat(stamp).astimezone().strftime("%m-%d %H:%M") if stamp else ""


def error_text(error: Exception) -> str:
    """Kind and next step only; raw messages may carry paths or values."""
    if isinstance(error, PermissionDenied):
        return "권한이 없습니다. 정의는 프로젝트 편집자가, 진행은 담당자 본인이 바꿉니다."
    if isinstance(error, ValidationError):
        return "입력을 확인하세요. 완료에는 근거가, 보류와 반려에는 사유가 필요합니다. 기한은 YYYY-MM-DD입니다."
    if isinstance(error, NotFound):
        return "대상을 찾을 수 없습니다. 새로 고친 뒤 다시 시도하세요."
    if isinstance(error, sqlite3.Error):
        return "저장하지 못했습니다. 다른 창이 쓰는 중이면 잠시 뒤 다시 시도하세요."
    return "처리하지 못했습니다."


def _link(reference: str | None) -> str:
    if not reference:
        return "-"
    safe = escape(reference, quote=True)
    return f'<a href="{safe}">{safe}</a>' if reference.startswith(("http://", "https://")) else safe


def detail_html(detail: dict, names: dict[str | None, str], person=lambda user_id: user_id) -> str:  # noqa: ANN001
    """Detail panel body. Everything typed by a person is escaped before it becomes HTML."""
    row = detail["row"]
    text = lambda value: escape(str(value)) if value not in (None, "") else "-"  # noqa: E731
    own = "<br><b>등록</b> 담당자 본인" if row.get("created_by") == row["assignee_id"] else ""
    lines = [
        f"<h3>{text(row['title'])}</h3>",
        f"<p><b>담당자</b> {text(person(row['assignee_id']))} · <b>기한</b> {text(row['due_date'])}<br>"
        f"<b>범위</b> {text(names.get(row['scope_id']))} · <b>분류</b> {text(names.get(row['category_id']))}<br>"
        f"<b>외부 참조</b> {_link(row['external_url'] or row['external_ref'])}{own}</p>",
        f"<p><b>상태</b> {state_label(row)} · {row['progress']}%"
        + (f" · {RESULT[row['result']]}" if row["result"] else "")
        + (f"<br><b>보류 사유</b> {text(row['hold_reason'])}" if row["hold_reason"] else "")
        + "</p>",
    ]
    if row["evidence_ref"]:
        lines.append(f"<p><b>완료 근거</b> {_link(row['evidence_ref'])}<br>{text(row['evidence_note'])}</p>")
    if detail["reviews"]:
        lines.append("<p><b>확인 기록</b></p><ul>")
        for review in reversed(detail["reviews"]):
            reason = f" — {text(review['reason'])}" if review["reason"] else ""
            lines.append(
                f"<li>{local_time(review['reviewed_at'])} {DECISION[review['decision']]} "
                f"({text(person(review['reviewer_id']))}){reason}</li>"
            )
        lines.append("</ul>")
    lines.append("<p><b>이력</b></p><ul>")
    for entry in reversed(detail["history"]):
        lines.append(f"<li>{local_time(entry['recorded_at'])} {STATUS[entry['status']]} {entry['progress']}%</li>")
    lines.append("</ul>" if detail["history"] else "<li>아직 없음</li></ul>")
    return "".join(lines)
