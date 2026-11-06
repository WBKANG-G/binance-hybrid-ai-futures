# Codex 최초 실행 지시문

너는 시니어 트레이딩 시스템 엔지니어, Python 백엔드 엔지니어, ML 엔지니어, 보안 리뷰어다.

현재 저장소의 `MASTER_SPEC_V1_0.md`를 완전히 읽고, 문서의 `[LOCKED]` 사항을 변경하지 마라. `[OPEN]` 위험 숫자는 LIVE의 기본값으로 추측하거나 하드코딩하지 마라. `LIVE_TRADING_ENABLED=false`를 유지하고 어떤 실거래 주문도 보내지 마라.

현재 작업 범위는 **Phase 0만**이다. 나머지 Phase는 사용자 승인 없이는 착수하지 마라.

Phase 0 구현 요구:
1. `apps/web`, `apps/api`, `services`, `integrations`, `packages`, `tests`, `infra`, `docs`가 있는 모노레포 뼈대 생성.
2. Python 3.12+ / FastAPI / Pydantic / PostgreSQL / React+TypeScript 기반 개발환경·Docker Compose 구성. 최신 호환 버전 확인, lockfile 고정.
3. `packages/domain`에 거래 모드, 신호, 주문, Algo 주문, 포지션, 위험 상태의 타입/계약 정의.
4. `configs/live.example.yaml`의 모든 필수 위험 항목을 null 또는 안전한 미설정 상태로 유지. 실거래 활성화 검사에서 반드시 거부되도록 구현.
5. `docs/decisions.md`, `docs/threat-model.md`, `docs/runbook.md`, `.env.example` 및 API 키 유출을 방지하는 `.gitignore` 작성.
6. pytest, Ruff, mypy, 프론트엔드 lint/typecheck 및 CI 워크플로 설치.
7. `docs/phase0-acceptance.md`에 검증 결과와 남은 문제 기록.

설계 전에 공식 바이낸스 최신 API 링크를 점검하고, 주문 `POST /fapi/v1/order`, 조건부 `POST /fapi/v1/algoOrder`, 신규 WebSocket `/public`, `/market`, `/private`의 구분을 검증하라. 이번 단계에서는 해당 외부 API에 실거래 요청을 보내지 마라.

완료 후 아래 형식으로 보고:
- 작성/변경 파일
- 주요 아키텍처 결정 및 미해결 사항
- 실행 방법
- 실제 실행한 테스트 명령과 결과
- 보안/안전성 점검 결과
- Phase 1 착수 전에 확인해야 할 사항

Phase 0가 끝났더라도 자동으로 Phase 1로 진행하지 마라.
