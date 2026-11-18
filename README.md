# WorkBoard

프로젝트별 업무 대상, 담당자, 기한, 진행 상태를 엑셀처럼 보고 함께 갱신하기 위한 독립 데스크톱 앱입니다. 모델·단위·통합 검증 업무는 첫 사용 사례입니다.

현재는 도메인 규칙, 로컬 SQLite 저장, Service, 그리고 PySide6 데스크톱 화면(담당자별 묶음 목록, 내 업무, 완료 근거 입력과 확인, 현황·속도)까지 구현되어 있습니다. 엑셀 가져오기, NAS 동기화, Codebeamer 연동은 아직 구현되지 않았습니다.

## 기본 실행

```bash
python3 -m venv .venv
# macOS/Linux: source .venv/bin/activate
# Windows: .venv\Scripts\activate
python3 -m pip install -e .
workboard --version
workboard            # 창을 엽니다
python3 -m unittest discover -s tests -v
```

화면 없이 Service까지만 시험하려면 PySide6 없이 `PYTHONPATH=src python -m unittest discover -s tests`를 실행합니다. 이때 화면 테스트는 건너뜁니다.

설정은 `config.example.toml`을 참고해 홈 폴더의 `.workboard/config.toml`에 둡니다. 없으면 기본값으로 동작합니다.

앱을 열면 ID와 비밀번호를 묻습니다. 그 PC에서 처음 열 때는 계정을 만듭니다. 계정은 그 PC의 로컬 DB에만 있습니다.

## 문서

- [제품 계획](docs/product-plan.md)
- [저장 및 동기화 설계](docs/storage-design.md)
- [검증 기준](docs/validation.md)
- [전체 작업 계획](docs/implementation-plan.md)
- [구성안과 데이터 계약](docs/architecture.md)
- [에이전트 공용 작업 공간](.agents/README.md)

## 데이터 취급

실제 프로젝트 정보, Codebeamer 데이터, NAS 주소 및 인증 정보는 저장소에 커밋하지 않습니다. 예시는 익명화된 값만 사용합니다.
