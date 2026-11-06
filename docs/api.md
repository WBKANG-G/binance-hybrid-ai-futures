# Phase 4 읽기 전용 개발 API

- `GET /api/v1/system/health`: API liveness, phase=4, PAPER/BACKTEST, LIVE/orders false,
  database=not_checked, as_of(UTC). DB나 거래 엔진 성공을 의미하지 않는다.
- `GET /api/v1/system/ready`: DB SELECT 1과 Alembic revision `0003_demo` 확인 시 200,
  실패 시 503/database=unavailable. 연결 timeout 2초.
- `GET /api/v1/market-data/status?symbol=BTCUSDT&interval=1m`: 공개 데이터 상태.
  as_of(호가), candle_as_of(있는 경우), observed_at, is_stale, reconciliation_required, reason,
  orders_enabled=false. 기록 없음은 NOT_STARTED/stale. DB 장애는 503/STORAGE_UNAVAILABLE.
  worker가 갱신을 멈춰도 현재 시각과 저장된 freshness budget으로 stale를 다시 계산한다.
- `/docs`, `/openapi.json`: 개발 API 계약.

키·서명·실제 잔고·LIVE 위험 설정을 반환하지 않는다. 주문·제어·위험 활성화 POST는 없으며 404다.
market 상태는 신규 주문 허가가 아니다. API는 로컬 개발용으로만 노출한다.

백테스트는 독립 CLI만 제공한다. 이 API가 연구 리포트를 실시간 계좌/거래 성과로 노출하거나 실행 명령을 받지 않는다.

- `GET /api/v1/paper/status`: 명시적 CLI 필요/worker_enabled=false/NOT_STARTED/stale를 반환한다.
- `GET /api/v1/paper/status?account_id=PAPER-...`: PAPER 전용 저장 snapshot의 as_of, is_stale,
  reconciliation_required, risk_state, policy_version, 열린 포지션 수량/보호 여부를 읽는다.
  과거 성공 replay도 시간이 지나면 stale이며 실행 worker를 의미하지 않는다. 누락 계좌/DB 오류는
  503/STORAGE_UNAVAILABLE_OR_ACCOUNT_MISSING, 잘못된 PAPER 계좌 패턴은 422다.

PAPER replay/복구는 `scripts.run_paper` CLI로만 명시적으로 실행한다. 주문/제어 HTTP POST는 계속 없다.

- Health의 demo_trading_enabled=false는 일반 API가 DEMO 거래를 활성화하지 않는다는 의미다.
- `GET /api/v1/demo/status`는 기본 NOT_ENABLED/stale/명시적 CLI 필요를 반환한다.
  `?account_id=DEMO-...`는 별도 저장 원장의 as_of, private_stream_fresh, is_stale,
  reconciliation_required, 위험/보호 snapshot을 읽는다. 실제 활성 세션을 API가 시작하지 않는다.
  API order routes 및 demo_trading_enabled는 false다. 누락 계좌/DB 오류는 정제된 503, 잘못된
  namespace는 422다. 실제 DEMO 수락 테스트 통과를 뜻하지 않는다.

DEMO 주문/명령/재개 HTTP POST는 없다. 인증 웹 제어는 후속 승인 단계다.
