# 1단계 도메인·로컬 SQLite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** NAS 없이 업무 생성·배정·진행 갱신·완료 확인이 로컬 SQLite에 저장되고, 속도와 현황을 이력에서 계산한다.

**Architecture:** 화면 → Service → Repository → SQLite. 규칙은 `domain`, SQL은 `storage`, 권한과 트랜잭션과 집계는 `services`에 둔다. 집계는 Repository가 돌려준 행을 Service가 파이썬으로 센다.

**Tech Stack:** Python 3.11+, 표준 라이브러리 `sqlite3`·`hashlib`·`json`·`unittest`. 의존성 없음.

**Spec:** `docs/superpowers/specs/2026-11-18-stage1-domain-storage-design.md`

## Global Constraints

- 의존성을 추가하지 않는다.
- ID는 UUIDv4 문자열, 시각은 UTC ISO-8601(마이크로초까지 고정 표기), 기한은 `YYYY-MM-DD`.
- Service 밖에서 SQL을 쓰지 않고, Service는 Repository만 부른다.
- 삭제는 삭제 표시와 취소로만 한다. 이력과 확인 기록은 추가만 된다.
- 쓰기 동작 하나는 `with conn:` 트랜잭션 하나다. 검사는 쓰기 전에 끝낸다.
- 테스트는 `python -m unittest discover -s tests`로 돌린다(`PYTHONPATH=src`).
- 커밋은 한글 Conventional Commits. push는 하지 않는다.

## Review Focus

1. 공백뿐인 근거 참조나 보류 사유 → 입력 오류. (Task 1)
2. 진행도가 정수가 아님(`True`, `50.0`, `"50"`) → 입력 오류. (Task 1)
3. 다른 프로젝트의 범위·분류를 업무에 붙임 → 입력 오류. (Task 3)
4. 확인된 완료를 진행 중으로 되돌렸다가 다시 완료 → 확인 대기. (Task 3)
5. 속도 기간의 끝이 시작보다 앞이거나 같음, 시간대 없는 시각 → 입력 오류. 없는 ID → 없음 오류. (Task 3, 4)

---

## 파일 구조

| 파일 | 책임 |
| --- | --- |
| `src/workboard/domain/errors.py` | `WorkBoardError`, `ValidationError`, `PermissionDenied`, `NotFound` |
| `src/workboard/domain/progress.py` | 열거형, `ProgressValues`, `validate_progress`, `validate_due_date`, `history_hash`, `review_state` |
| `src/workboard/storage/database.py` | `open_database(path)`: 연결, 외래 키, `user_version` 스키마 |
| `src/workboard/storage/repository.py` | `Repository(conn)`: 모든 SQL |
| `src/workboard/services/workboard.py` | `WorkBoardService(conn, clock)`: 권한, 트랜잭션, 조회 집계 |
| `tests/support.py` | 임시 DB와 고정 시계로 Service를 만드는 도우미 |

### Task 1: 도메인 규칙

**Files:** Create `domain/errors.py`, `domain/progress.py`, `tests/test_progress_rules.py`. Modify `domain/__init__.py`.

**Interfaces (Produces):**
- `Status`: `NOT_STARTED="not_started"`, `IN_PROGRESS="in_progress"`, `ON_HOLD="on_hold"`, `DONE="done"`
- `Result`: `PASS="pass"`, `FAIL="fail"`, `NOT_APPLICABLE="na"`
- `Decision`: `CONFIRMED="confirmed"`, `REJECTED="rejected"`
- `ReviewState`: `NONE="none"`, `PENDING="pending"`, `CONFIRMED="confirmed"`, `REJECTED="rejected"`
- `ProgressValues(status, progress, result=None, hold_reason=None, evidence_ref=None, evidence_note=None)` frozen dataclass
- `validate_progress(values) -> ProgressValues`: 규칙 표를 검사하고 정리한 값을 돌려준다. 문자열은 앞뒤 공백을 지우고 빈 값은 `None`. 상태에 해당하지 않는 칸(완료가 아닌데 근거·결과, 보류가 아닌데 사유)은 `None`으로 비운다.
- `validate_due_date(value: str | None) -> str | None`
- `history_hash(prev_hash: str, fields: dict) -> str`: `sha256(prev_hash + json.dumps(fields, sort_keys=True, ensure_ascii=False, separators=(",", ":")))`의 16진 문자열
- `review_state(status: str, decision: str | None) -> ReviewState`

- [ ] 테스트 작성: 상태별 통과 값, 규칙 표 위반 각각, Review Focus 1·2, 기한(`2026-01-15` 통과, `2026-1-5`·`2026-02-30`·`01/15` 거부), 해시가 같은 입력에 같고 `prev_hash`나 필드가 바뀌면 다름, `review_state` 네 경우
- [ ] 실패 확인 → 구현 → 통과 확인 → 커밋 `feat: 진행 상태 규칙과 이력 해시 추가`

### Task 2: 스키마와 Repository

**Files:** Create `storage/database.py`, `storage/repository.py`, `tests/test_storage.py`, `tests/support.py`.

**Interfaces (Produces):**
- `open_database(path: str | Path) -> sqlite3.Connection` (`row_factory=sqlite3.Row`, `PRAGMA foreign_keys=ON`, `user_version` 1)
- 테이블: `project`, `project_editor`, `scope`, `category`, `work_item`, `assignment`, `assignment_progress`, `progress_history`, `completion_review`, `sync_outbox`
- `progress_history`와 `completion_review`는 `seq INTEGER PRIMARY KEY AUTOINCREMENT`와 UUID `id`를 갖고, UPDATE·DELETE를 `RAISE(ABORT)` 트리거로 막는다.
- `sync_outbox` 키: `(kind, project_id, author_id)`, `kind`는 `definition`·`progress`·`review`, 정의의 `author_id`는 `''`.
- `Repository.assignment_rows(project_id=None, assignee_id=None, scope_id=None, category_id=None, assignment_id=None) -> list[dict]`: 취소·삭제를 뺀 배정 행. 열: `assignment_id, project_id, work_item_id, title, scope_id, category_id, external_ref, external_url, assignee_id, due_date, status, progress, result, hold_reason, evidence_ref, evidence_note, reported_at, history_id, review_decision, review_reason, reviewed_at`. 진행 행이 없으면 `status="not_started"`, `progress=0`. `reported_at`·`history_id`는 최신 이력 행, `review_*`는 그 행의 가장 나중 확인 기록.
- `Repository.rejected_rows(project_id=None) -> list[dict]`: 유효 확인 기록이 반려인 이력 행의 `project_id, author_id, reviewed_at`(취소·삭제 제외).
- 그 밖의 삽입·단건 조회 메서드는 Task 3이 쓰는 대로 둔다.

- [ ] 테스트 작성: 새 파일에 스키마가 생기고 `user_version`이 1, 닫았다 다시 열어도 그대로이고 데이터가 남음, 이력과 확인 기록의 UPDATE·DELETE가 `sqlite3.IntegrityError`, 없는 프로젝트를 가리키는 업무 삽입이 외래 키 오류
- [ ] 실패 확인 → 구현 → 통과 확인 → 커밋 `feat: 로컬 SQLite 스키마와 Repository 추가`

### Task 3: Service 쓰기 동작

**Files:** Create `services/__init__.py`, `services/workboard.py`, `tests/test_service_writes.py`.

**Interfaces (Produces):** `WorkBoardService(conn, clock=None)`. `clock`은 시간대가 있는 `datetime`을 돌려주는 함수다.
- `create_project(actor, name, description="") -> str` (만든 사람이 편집자)
- `add_editor(actor, project_id, user_id) -> None`
- `create_scope(actor, project_id, name, kind="", parent_id=None) -> str`
- `create_category(actor, project_id, name, position=0) -> str`
- `create_work_item(actor, project_id, title, scope_id=None, category_id=None, external_ref=None, external_url=None) -> str`
- `delete_work_item(actor, work_item_id) -> None`
- `assign(actor, work_item_id, assignee_id, due_date=None) -> str`
- `set_due_date(actor, assignment_id, due_date) -> None`
- `cancel_assignment(actor, assignment_id) -> None`
- `update_progress(actor, assignment_id, status, progress, result=None, hold_reason=None, evidence_ref=None, evidence_note=None) -> bool` (바뀐 것이 없으면 `False`)
- `review_completion(actor, assignment_id, decision, reason=None) -> None`
- `verify_history(project_id, author_id) -> bool`
- `pending_sync_count() -> int`

- [ ] 테스트 작성:
  - 재시작 후 보존(닫고 다시 열어 배정·진행·이력 조회)
  - 권한: 편집자 아닌 사람의 정의 변경 6종, 담당자 아닌 사람(편집자 포함)의 진행 변경, 편집자 아닌 사람의 확인, 자기 배정 확인 → `PermissionDenied`이고 행 수 불변
  - 입력: 빈 이름·제목·담당자, 근거 없는 완료, 사유 없는 반려, 완료가 아닌 배정 확인, 취소·삭제된 배정의 진행 변경, Review Focus 3, 없는 ID → `NotFound`
  - 원자성: 진행 변경 한 번에 진행·이력 1행·전송 대기 `progress` 1건. `mark_dirty`가 예외를 내게 하면 셋 다 없음
  - 같은 값으로 다시 저장하면 `False`이고 이력이 늘지 않음
  - 사슬: 두 번 변경 후 `verify_history`가 참, 두 번째 행의 `prev_hash`가 첫 행의 `hash`
  - 전송 대기: 정의 변경은 `definition`, 확인은 `review`, 같은 키는 한 번만
- [ ] 실패 확인 → 구현 → 통과 확인 → 커밋 `feat: 업무·배정·진행·완료 확인 Service 추가`

### Task 4: Service 조회

**Files:** Modify `services/workboard.py`. Create `tests/test_service_reads.py`.

**Interfaces (Produces):**
- `list_projects()`, `list_scopes(project_id)`, `list_categories(project_id)` → `list[dict]`
- `list_assignments(project_id, scope_id=None, category_id=None, assignee_id=None) -> list[dict]` (Task 2의 열 + `review_state`)
- `my_assignments(actor) -> list[dict]`
- `assignment_detail(assignment_id) -> dict` (`row`, `history`, `reviews`)
- `pending_reviews(actor, project_id) -> list[dict]` (확인 대기이고 담당자가 `actor`가 아닌 행)
- `group_summary(project_id, by, today) -> list[dict]`: `by`는 `"assignee"` 또는 `"category"`. 열 `key, count, average_progress, confirmed, pending, rejected, overdue`
- `project_status(project_id, today) -> dict`: `total, confirmed, pending, on_hold, overdue, by_category`
- `assignee_pace(start, end, project_id=None) -> list[dict]`: 열 `assignee_id, assigned, reported, confirmed, rejected, per_week, weekly`
- `project_pace(start, end) -> list[dict]`: 열 `project_id, name, total, confirmed_total, remaining, confirmed, per_week, weeks_left, weekly`

`today`는 `YYYY-MM-DD` 문자열, `start`·`end`는 시간대가 있는 `datetime`이다. `weekly`는 `start`부터 7일씩 끊은 구간별 확인된 완료 수이고 구간 수는 `ceil(기간 일수 / 7)`이다. `per_week`는 확인된 완료 수 ÷ (기간 초 ÷ 604800). 확인된 완료의 시점은 `reported_at`이다.

- [ ] 테스트 작성: 편집자 2명·담당자 2명·업무 6건의 예시를 고정 시계로 만들고 손으로 센 값과 비교
  - 확인 상태 흐름: 확인 대기 → 확인됨, 반려됨 → 근거 수정 후 확인 대기, 확인됨 → 근거 수정 후 확인 대기, Review Focus 4
  - `pending_reviews`가 자기 배정을 빼는지
  - `group_summary` 두 기준, `project_status`(기한 초과는 기한 < 오늘이고 완료 아님, 취소·삭제 제외)
  - `assignee_pace`(프로젝트 하나와 전체), `project_pace`(평균 0이면 `weeks_left`가 `None`), 정정된 반려는 세지 않음, Review Focus 5
- [ ] 실패 확인 → 구현 → 통과 확인 → 커밋 `feat: 목록·현황·속도 조회 추가`

### Task 5: 프로젝트 문서 갱신

**Files:** Modify `docs/architecture.md`, `docs/product-plan.md`, `docs/implementation-plan.md`, `docs/validation.md`, `README.md`, `.agents/STATUS.md`, `src/workboard/main.py`(안내 문구). Create `.agents/sessions/2026-11-18-stage1-domain-storage.md`.

- [ ] 설계 문서의 "구현하면서 함께 고칠 문서" 목록대로 반영. `STATUS.md`는 기본 브랜치 기준만 적으므로 이번 브랜치 내용은 세션 기록에 둔다.
- [ ] 전체 테스트와 `python -m workboard --version` 실행 결과를 세션 기록에 적는다.
- [ ] 커밋 `docs: 1단계 구현 내용과 화면 구성 반영`
