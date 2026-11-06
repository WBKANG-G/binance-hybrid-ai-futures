# Phase 3 파일 목록

기존 Phase 0~2 작업은 보존했고 AGENTS.md / MASTER_SPEC_V1_0.md / CODEX_START_HERE.md는 변경하지 않았다.

| 구분 | 파일 | 내용 |
|---|---|---|
| 신규 | services/risk/paper.py | PAPER 전용 정책, snapshot, sizing, 기간/평가액 |
| 신규 | services/execution/models.py, paper.py, simulator.py, status.py | 영속 상태, 조정자, 시뮬레이터, 읽기 상태 |
| 신규 | services/protection/paper.py | 보호의 방향/가격/수량/상태 검증 |
| 신규 | packages/persistence/paper.py | 계좌 잠금, lease/fencing, venue, 감사/outbox |
| 신규 | migrations/versions/0002_paper.py | 독립 스키마 snapshot, upgrade/downgrade |
| 신규 | configs/paper-sandbox.example.yaml, paper-sandbox.schema.json | 명시적인 PAPER 합성 가정 |
| 신규 | scripts/run_paper.py | 별도 CLI 재현/영속 복구/결과 파일 |
| 신규 | tests/integration/test_paper.py | 실제 PostgreSQL 동시성·장애·손익·보호 검증 |
| 수정 | migrations/env.py, packages/persistence/health.py | 새 스키마 등록과 ready revision |
| 수정 | apps/api/main.py, apps/web/src/App.tsx, App.test.tsx | Phase 3 상태, 읽기 전용 PAPER 상태 |
| 수정 | packages/domain/config.py | Phase 3 차단 메시지, 기존 안전 게이트 유지 |
| 수정 | scripts/smoke.py, generate_schemas.py, Makefile | smoke, schema, 명시적 PAPER 명령 |
| 수정 | tests/integration/test_api.py, tests/contract/test_schema.py | Phase 3 상태/안전 계약 |
| 수정 | pyproject.toml, .github/workflows/ci.yml | 설명·타입검사 대상·Phase 3 CI 이름 |
| 수정/신규 | README.md, docs/runbook.md, docs/api.md, docs/decisions.md, docs/phase3-*.md, services 관련 README | 실행·설계·실제 검증 기록 |

의존성/uv.lock/npm lockfile 변경 없음. infra 구성/기존 DB volume/0001 migration 보존.
미커밋 상태의 기존 작업 위에 추가했으며 commit/push는 수행하지 않았다.
