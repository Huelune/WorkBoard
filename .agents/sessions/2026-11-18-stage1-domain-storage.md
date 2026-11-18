# 1단계 도메인·로컬 SQLite 구현

- 날짜: 2026-11-18
- 담당 작업 세션: WorkBoard 1단계 구현
- 브랜치 및 기준 커밋: feat/repo-analysis-f4b38f, 기준 10dccc2
- 작업 상태: 완료(브랜치에만 있음, main 미반영)

## 목표와 범위

구현 계획 1단계를 구현한다. NAS 없이 업무 생성·배정·진행 갱신이 로컬에 저장된다. 사용자가 추가한 요구인 완료 근거, 편집자 확인, 담당자별·프로젝트별 속도를 포함한다. 화면, 엑셀 입출력, NAS 공유는 범위 밖이다.

## 변경 사항과 주요 파일

- docs/superpowers/specs/2026-11-18-stage1-domain-storage-design.md: 승인된 설계와 결정
- docs/superpowers/plans/2026-11-18-stage1-domain-storage.md: 구현 계획
- src/workboard/domain: 오류 종류, 상태·진행 규칙, 기한 검사, 이력 해시
- src/workboard/storage: 스키마(버전 1), 추가 전용 트리거, Repository
- src/workboard/services: 권한, 트랜잭션, 완료 확인, 목록·현황·속도 조회
- docs/architecture.md, product-plan.md, implementation-plan.md, validation.md, README.md: 근거·확인·속도와 화면 구성 반영

## 실행한 검증

- `PYTHONPATH=src python -m unittest discover -s tests`: 44개 통과(Python 3.14)
- `PYTHONPATH=src python -m workboard --version`: WorkBoard 0.1.0 출력
- 조회 테스트는 구현 전 실패를 따로 실행해 보지 못했다(메서드가 없던 상태에서 구현과 함께 처음 실행).

## Git 및 외부 반영

- 커밋: 설계 2건, 기능 4건, 문서 1건. 모두 로컬 브랜치에만 있다.
- push, PR 없음. Notion 기록은 갱신하지 않았다.
- .agents/STATUS.md는 main 기준 문서라 고치지 않았다.

## 남은 사항과 다음 단계

- 0단계 결정표의 가정(한 행 = 한 배정, 진행도 0~100, 사용자 ID는 문자열)은 아직 가정이다.
- 편집자 해제, 표시 이름, 업무 크기 가중치는 범위에서 뺐다.
- 다른 PC가 과거 이력 변경을 알아채는 것은 4단계에서 구현한다.
- 다음은 2단계 화면이다. 시작 전에 대상 OS와 PySide6 설치 제약을 확인한다.
