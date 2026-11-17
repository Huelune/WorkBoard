# WorkBoard

프로젝트별 업무 대상, 담당자, 기한, 진행 상태를 엑셀처럼 보고 함께 갱신하기 위한 독립 데스크톱 앱입니다. 모델·단위·통합 검증 업무는 첫 사용 사례입니다.

현재는 프로젝트 기반 구조와 공통 작업 ID 규칙만 구현되어 있습니다. 엑셀형 화면, SQLite 저장, NAS 동기화, Codebeamer 연동은 아직 구현되지 않았습니다.

## 기본 실행

```bash
python3 -m venv .venv
# macOS/Linux: source .venv/bin/activate
# Windows: .venv\Scripts\activate
python3 -m pip install -e .
workboard --version
python3 -m unittest discover -s tests -v
```

## 문서

- [제품 계획](docs/product-plan.md)
- [저장 및 동기화 설계](docs/storage-design.md)
- [검증 기준](docs/validation.md)
- [전체 작업 계획](docs/implementation-plan.md)
- [구성안과 데이터 계약](docs/architecture.md)
- [에이전트 공용 작업 공간](.agents/README.md)

## 데이터 취급

실제 프로젝트 정보, Codebeamer 데이터, NAS 주소 및 인증 정보는 저장소에 커밋하지 않습니다. 예시는 익명화된 값만 사용합니다.
