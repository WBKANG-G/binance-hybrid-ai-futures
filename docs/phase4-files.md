# Phase 4 변경 파일

이전 작업과 보호 문서는 보존했다. dependency/lockfile/infra volume 변경 없음, commit/push 없음.

| 구분 | 파일 | 내용 |
|---|---|---|
| 신규 | integrations/binance_usdm/demo.py, demo_snapshot.py | DEMO 전용 전송·서명·세션·실제 REST snapshot |
| 신규 | services/execution/demo.py, demo_models.py, demo_stream.py, demo_status.py | 위험/실행/보호/제어·상태·private 감시 |
| 신규 | packages/persistence/demo.py, migrations/versions/0003_demo.py | 별도 계좌/이벤트/outbox·lease/fence |
| 신규 | configs/demo.example.yaml, configs/demo.schema.json | DEMO 전용 설정, 쓰기 기본 차단 |
| 신규 | scripts/run_demo.py, scripts/demo_probe.py | 명시적 worker/preflight/명령·공개 진단 |
| 신규 | tests/fixtures/demo.py | 합성 Binance 응답, 외부 수락 증거 아님 |
| 신규 | tests/integration/test_demo.py, tests/unit/test_demo_transport.py, tests/fault_injection/test_demo_stream.py | 실제 PostgreSQL/localhost WS·격리·장애·CLI 거부 |
| 수정 | services/risk/paper.py, services/execution/models.py | mode를 검증하는 공유 위험 수학, server/Algo/exit 복구 필드 |
| 수정 | packages/persistence/paper.py, migrations/env.py, packages/persistence/health.py | PAPER mode 경계·별도 schema 등록·revision 0003 ready |
| 수정 | scripts/generate_schemas.py, tests/contract/test_schema.py, tests/conftest.py | DEMO schema·테스트 credential 격리 |
| 수정 | apps/api/main.py, apps/web/src/App.tsx, App.test.tsx, tests/integration/test_api.py, scripts/smoke.py | Phase 4 상태·DEMO 기본 차단·조회만 제공 |
| 수정 | packages/domain/config.py, pyproject.toml, .github/workflows/ci.yml | 차단 메시지·범위/설명·CI 이름 |
| 수정/신규 | README.md, docs/api.md, docs/runbook.md, docs/decisions.md, docs/phase4-*.md, 관련 service/integration README | 진행 상태·설계·검증·재개 지침 |

환경 draft에는 기존 허용 목록을 보존하여 DEMO REST/WSS 두 호스트를 추가하고 direct runtime 키 변수
요구사항을 저장했다. 시작 지침도 Phase 4 진행 상태/재개 절차로 갱신한다. 실제 키 값/게시 결과는 저장하지 않았다.
