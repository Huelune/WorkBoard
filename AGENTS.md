# WorkBoard 프로젝트 규칙

- 이 저장소는 IntraFlow와 별개입니다. 참고 범위는 PC별 로컬 SQLite와 NAS JSON 스냅샷 저장 방식입니다.
- WorkBoard는 공통 업무 도구입니다. 검증 분야와 Codebeamer는 첫 사용 사례이며 모든 작업에 필수인 도메인이 아닙니다.
- SQLite 파일을 NAS에 두거나 DB 파일을 통째로 동기화하지 않습니다.
- UI는 Service를 호출하고, Service는 Repository를 호출합니다. NAS 파일 접근은 sync 모듈에서만 수행합니다.
- 로컬 저장 성공과 NAS 공유 성공을 구분합니다. NAS 장애가 로컬 작업 저장을 취소하면 안 됩니다.
- 프로젝트·업무 정의는 프로젝트 편집자가 관리하고, 담당자는 자신의 진행 상태와 결과만 수정합니다.
- 같은 제목 또는 외부 참조라도 범위가 다르면 별개의 WorkItem ID를 사용합니다.
- 실제 업무 데이터, 사용자 식별 정보, NAS 주소 및 인증 정보는 커밋하지 않습니다.
- 구현 완료를 주장하기 전에 실제 실행 및 테스트 결과를 확인합니다.
- 커밋은 한글 Conventional Commits 형식을 사용합니다.
- push, PR, 배포는 사용자의 명시적 요청이 있을 때 수행합니다.
- 다른 에이전트와 작업을 공유할 때는 `.agents/README.md`의 시작·인수인계 절차를 따릅니다. 같은 파일의 동시 편집과 타인의 미커밋 변경 덮어쓰기를 피합니다.

## 문서

- 제품 범위와 결정: docs/product-plan.md
- 저장 및 동기화: docs/storage-design.md
- 검증 기준: docs/validation.md
- 에이전트 공용 작업 공간: .agents/README.md

## Notion

프로젝트 문서와 전용 데이터베이스를 실제로 확인한 뒤 아래 위치를 사용합니다.

- 개발 포트폴리오: https://app.notion.com/p/3b1f5e6cf897818d9b4be7a92ed11508
- 프로젝트 데이터베이스: https://app.notion.com/p/23c44f4364684707837b03ebd3284d27
- 표준 프로젝트 문서: https://app.notion.com/p/41bf5e6cf897817ab2cafe377f10287b
- 개발 작업 이력 데이터베이스: https://app.notion.com/p/41bf5e6cf897819eb90ff3dfa0c1e1c3
- 개발 작업 이력 데이터 소스: collection://41bf5e6c-f897-81f9-a0c9-000bbccc75e2
- 작업 백로그 데이터베이스: https://app.notion.com/p/41bf5e6cf897810fb8fcd0032c7cc1b4
- 작업 백로그 데이터 소스: collection://41bf5e6c-f897-816d-bef6-000b45ee59ef
- 포트폴리오 데이터베이스: https://app.notion.com/p/41bf5e6cf8978167af69ee19a21d2bc6
- 포트폴리오 데이터 소스: collection://41bf5e6c-f897-8180-8a28-000bf1e84c6b
