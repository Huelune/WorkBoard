"""Korean labels and small text helpers. No Qt here, so it is testable on its own."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from html import escape

from workboard.domain import NotFound, PermissionDenied, ValidationError, is_overdue, is_web_link

STATUS = {"not_started": "미시작", "in_progress": "진행 중", "on_hold": "보류", "done": "완료"}
REVIEW = {"pending": "확인 대기", "confirmed": "확인됨", "rejected": "반려됨"}
RESULT = {"pass": "통과", "fail": "실패", "na": "해당 없음"}
DECISION = {"confirmed": "확인", "rejected": "반려"}
IMPORT_VERDICTS = {"new": "새로 등록", "duplicate": "이미 있음", "error": "오류", "blank": "빈 줄"}
IMPORT_REASONS = {
    "no_scope": "첫째 분류 칸이 비어 있음",
    "no_category": "둘째 분류 칸이 비어 있음",
    "no_assignee": "담당자가 비어 있음",
    "unknown_assignee": "구성원 목록에 없는 담당자. 관리 화면에서 구성원의 ID나 표시 이름을 맞춰 주세요",
    "ambiguous_assignee": "같은 표시 이름의 구성원이 여럿. 엑셀에 ID를 적거나 표시 이름을 다르게 해 주세요",
    "deleted_scope": "삭제된 항목과 같은 이름(첫째 분류 칸)",
    "deleted_category": "삭제된 항목과 같은 이름(둘째 분류 칸)",
    "bad_due_date": "기한을 날짜로 읽을 수 없음",
    "bad_link": "링크가 http:// 또는 https://로 시작하지 않음",
}
EXCEL_ERRORS = {
    "not_installed": "이 PC에서 엑셀을 실행하지 못했습니다. 엑셀에서 칸을 복사한 뒤 '복사한 칸 붙여 넣기'를 써 보세요.",
    "cannot_open": (
        "엑셀이 파일을 열지 못했습니다. 엑셀에 떠 있는 안내 창이 있으면 닫고, 엑셀에서 그 파일이 직접 열리는지 "
        "확인한 뒤 다시 시도하세요. 계속 안 되면 칸을 복사해 '복사한 칸 붙여 넣기'를 쓰세요."
    ),
    "no_sheets": "읽을 시트가 없습니다.",
}
DEFAULT_LABELS = ("대상 버전", "업무 분야")


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


def local_time(stamp: str | None) -> str:
    return datetime.fromisoformat(stamp).astimezone().strftime("%m-%d %H:%M") if stamp else ""


# What each refusal means for the person, keyed by the Service's own message.
# Anything not listed falls back to a short sentence for its kind.
_REFUSALS = {
    "progress does not match the status": "진행도가 상태와 맞지 않습니다. 미시작은 0, 완료는 100, 그 밖은 0~99입니다.",
    "hold reason is required": "보류 사유를 적어 주세요.",
    "evidence reference is required": "완료 근거를 적어 주세요. 결과 문서의 링크나 항목 ID입니다.",
    "reason is required": "반려 사유를 적어 주세요.",
    "title is required": "제목이 빈 줄이 있습니다. 제목을 적거나 그 줄을 지워 주세요.",
    "name is required": "이름을 적어 주세요.",
    "assignee is required": "담당자를 골라 주세요.",
    "user is required": "사용자 ID를 적어 주세요.",
    "scope label is required": "첫째 칸 이름을 적어 주세요.",
    "category label is required": "둘째 칸 이름을 적어 주세요.",
    "due date must be YYYY-MM-DD": "기한은 YYYY-MM-DD 형식의 실제 날짜여야 합니다.",
    "external link must start with http:// or https://": "링크는 http:// 또는 https://로 시작해야 합니다.",
    "both grouping fields are required for new work": "위쪽 두 칸의 항목을 모두 골라 주세요.",
    "at least one work item is required": "등록할 업무를 한 줄 이상 적어 주세요.",
    "no account with that ID": "그 ID로 만든 계정이 없습니다. 먼저 본인이 로그인 창에서 계정을 만든 뒤에 추가하세요.",
    "a project needs at least one editor": "편집자는 한 명 이상 남아야 합니다.",
    "remove the editor role first": "편집자는 역할을 먼저 해제해야 뺄 수 있습니다.",
    "that member is not an editor": "그 구성원은 편집자가 아닙니다.",
    "only a completed assignment can be reviewed": "완료로 신고된 업무만 확인할 수 있습니다. 새로 고친 뒤 다시 보세요.",
    "assignment is cancelled or its work item is deleted": "취소되었거나 삭제된 업무입니다. 새로 고친 뒤 다시 보세요.",
    "work item is deleted": "삭제된 업무입니다. 새로 고친 뒤 다시 보세요.",
    "only the assignee may change progress": "권한이 없습니다. 진행은 담당자 본인만 바꿀 수 있습니다.",
    "you may not review your own work": "권한이 없습니다. 자기가 담당한 업무는 확인할 수 없습니다.",
    "only a project editor may change definitions": "권한이 없습니다. 프로젝트 편집자만 바꿀 수 있습니다.",
    "only a project editor may review a completion": "권한이 없습니다. 완료 확인은 프로젝트 편집자만 할 수 있습니다.",
    "members may add work only for themselves": "권한이 없습니다. 구성원은 자기 자신의 업무만 등록할 수 있습니다.",
    "only project members may add work": "권한이 없습니다. 이 프로젝트의 구성원만 업무를 등록할 수 있습니다.",
}
# Which input a refusal is about, so the form can put the cursor there.
_FIELDS = {
    "progress does not match the status": "progress",
    "hold reason is required": "hold_reason",
    "evidence reference is required": "evidence_ref",
}


def error_text(error: Exception) -> str:
    """What went wrong and what to do, in the person's words. Never the raw message:
    it is written for developers and an unexpected one may carry paths or values."""
    known = _REFUSALS.get(str(error))
    row = getattr(error, "row", None)
    if known and row:
        return f"{row}번째 업무: {known}"
    if known:
        return known
    if isinstance(error, PermissionDenied):
        return "권한이 없습니다. 정의는 프로젝트 편집자가, 진행은 담당자 본인이 바꿉니다."
    if isinstance(error, ValidationError):
        if "was deleted" in str(error):
            return "삭제된 항목은 새 업무에 쓸 수 없습니다. 다른 항목을 골라 주세요."
        return "입력을 확인하세요. 빠졌거나 형식이 맞지 않는 칸이 있습니다."
    if isinstance(error, NotFound):
        return "대상을 찾을 수 없습니다. 새로 고친 뒤 다시 시도하세요."
    if isinstance(error, sqlite3.Error):
        return "저장하지 못했습니다. 다른 창이 쓰는 중이면 잠시 뒤 다시 시도하세요."
    return "처리하지 못했습니다."


def error_field(error: Exception) -> str | None:
    return _FIELDS.get(str(error))


def _link(reference: str | None) -> str:
    if not reference:
        return "-"
    safe = escape(reference, quote=True)
    return f'<a href="{safe}">{safe}</a>' if is_web_link(reference) else safe


def detail_html(  # noqa: ANN001
    detail: dict, names: dict[str | None, str], person=lambda user_id: user_id, labels=DEFAULT_LABELS
) -> str:
    """Detail panel body. Everything typed by a person is escaped before it becomes HTML."""
    row = detail["row"]
    text = lambda value: escape(str(value)) if value not in (None, "") else "-"  # noqa: E731
    own = "<br><b>등록</b> 담당자 본인" if row.get("created_by") == row["assignee_id"] else ""
    lines = [
        f"<h3>{text(row['title'])}</h3>",
        f"<p><b>담당자</b> {text(person(row['assignee_id']))} · <b>기한</b> {text(row['due_date'])}<br>"
        f"<b>{text(labels[0])}</b> {text(names.get(row['scope_id']))} · <b>{text(labels[1])}</b> {text(names.get(row['category_id']))}<br>"
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
