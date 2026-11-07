# Phase 1 — Binance 어댑터/시장 데이터 검증 보고서

검증일: 2026-10-19 (Asia/Seoul).
**Phase 1 구현 및 로컬 수락 검사 통과. 실제 Binance 공개망 연결 검증은 proxy 403으로 보류. Phase 2 미착수.**

## 작성·변경 파일과 구현

[전체 Phase 1 파일 목록](phase1-files.md).

- `integrations/binance_usdm`: GET-only REST, HMAC/시간 동기화 primitive, tick/step/가격/notional 필터,
  일반/Algo 오프라인 주문 계약, routed WS·재연결, private 이벤트와 listenKey lease 계약.
- `services/market_data`: Decimal/UTC 캔들·호가 정규화, 확정봉 검증, 누락·역순·중복·충돌 처리,
  REST snapshot bridge/pu 검사, freshness, 중단·재접속 시 신뢰 무효화, 제한 시간 수집기.
- `packages/persistence/market.py`, `migrations`, `alembic.ini`: raw event hash unique, canonical closed candles,
  synchronized best quotes, stream status. NUMERIC 고정 scale 없이 Decimal 저장. versioned schema snapshot.
- `scripts/collect_market.py`, `backfill_market.py`: 명시적 종목과 기간/시간 제한의 공개 데이터 명령.
- API: phase=1 표시, DB와 migration revision readiness, 공개 데이터 상태 조회.
- web: Phase 1 개발 상태 표시만 갱신. 거래 대시보드·전략 UI로 범위를 확대하지 않음.
- Docker/Makefile/CI: migration 및 추가 모듈·검사 포함. lockfile에 websockets 17.2 고정.
- README·설계·Runbook·API·위협모델·공식 API 재검토 문서 갱신.

`MASTER_SPEC_V1_0.md`, `AGENTS.md`, `CODEX_START_HERE.md` 변경 없음.
원래 Phase 0 파일/변경을 보존했으며 임의 reset·삭제·commit·push를 수행하지 않았다.

## 필수 수락 기준

| 기준 | 결과와 근거 |
|---|---|
| 공식 주문 파라미터 계약 | PASS: LIMIT 일반 주문과 CONDITIONAL Algo endpoint/ID 분리, close-all 불법 조합 미포함, 클라이언트 ID 검사, 변조된 모델 재검증 |
| REST 서명 | PASS: 독립된 HMAC 표준 test vector, canonical query encoding, reserved field 주입·잘못된 timestamp/window 거부, SecretStr masking |
| tick/step/price 필터 | PASS: PRICE_FILTER·LOT_SIZE·MIN_NOTIONAL·PERCENT_PRICE, metadata 누락/비적격 symbol 거부. display precision 미사용 |
| public/market/private WS 계약 | PASS: route 분리, mixed subscription 거부, 일반/Algo 이벤트 필드 분리, listenKey lease/만료/중복/역순 검사. 실제 private 인증 접속은 미실행 |
| 단절·재접속 | PASS: 실제 localhost WebSocket handshake/frame/close/reconnect, REST 재동기화 선행, backoff, cancel 중 작업 완료 후 최종 disconnect |
| 중복·역순·결측 | PASS: 확정봉 충돌 자동 덮어쓰기 금지, gap/reverse fail-closed, raw 보존, depth U/u bridge 및 pu 연속성, 0 수량 삭제·crossed book 거부 |
| as_of/freshness | PASS: UTC/미래·stale 시각 검사, API가 worker 정지 뒤 freshness를 재계산, NOT_STARTED/DISCONNECTED를 정상으로 표시하지 않음 |
| DB migration | PASS: 실제 PostgreSQL 임시 schema에서 upgrade/downgrade/re-upgrade, 모델 drift 검사, raw/candle 중복, 24자리 소수 Decimal 일치 |

## 실제 실행 명령과 결과

작업 디렉터리: `/workspace/binance-hybrid-ai-futures`.
DB 검사에는 `TEST_DATABASE_URL=postgresql+psycopg://dev:dev@127.0.0.1:5432/hybrid`,
uv cache에는 workspace 내 경로를 지정했다.

| 명령 | 실제 결과 |
|---|---|
| `uv lock`, `uv sync --frozen`, `uv lock --check` | 설치/고정 lock 검증 통과 |
| `uv run --frozen pytest -q` | **113 passed, 0 failed, 0 skipped**. 실제 PostgreSQL/localhost WS 포함 |
| `uv run --frozen ruff check .`, `ruff format --check .` | 통과 |
| `uv run --frozen mypy` | 통과, 29 source files |
| `npm --prefix apps/web run lint`, `run typecheck`, `run build` | 통과 |
| `npm --prefix apps/web test` | **5 passed**, JSDOM 기반 |
| `uv run --frozen alembic upgrade head`, `alembic check` | 실제 개발 DB에 적용, 재실행 idempotent, drift 없음 |
| `python -m scripts.compose_cloud up -d --build --wait` | API/web 이미지 빌드 및 DB/API/web healthy |
| `python -m scripts.smoke` | health, migration readiness, web HTML, API proxy, market-data status 계약 통과 |
| Compose API/web restart 후 `up -d --no-build --wait`, smoke | 재시작 뒤 재통과 |
| `--network none` 이미지의 LIVE/true 시작 | 예상대로 exit 3. 미확정 위험값을 거부, 주문 요청 없음 |
| `python -m scripts.collect_market --symbol BTCUSDT --seconds 2` | **exit 2**, 동기화된 공개 데이터 미확보. DB 상태 DISCONNECTED/stale/orders=false 확인 |
| Binance time / public WS 연결 진단 | 양쪽 모두 **proxy CONNECT 403**. 실제 Binance 시세 수신은 검증하지 못함 |
| `git diff --check`, 보호 기준 문서 diff | 통과, 보호 문서 변경 없음 |

localhost WS fixture, 합성 데이터 및 임시 schema 결과를 Binance 서버의 실제 시세·계좌·체결 검증으로 표시하지 않는다.
GitHub Actions는 같은 검사를 구성했으며 호스팅 서버의 CI 실행은 미검증이다. 실제 브라우저 E2E도 수행하지 않았다.

## 안전성과 설계 결정

- LIVE/DEMO 시작·모든 주문 submit은 계속 거부한다. 위험 숫자 12개 모두 null이며 AI/웹이 수정할 API가 없다.
- 실제 HTTP는 고정 host의 공개 GET allowlist만 제공한다. order/algoOrder/listenKey/account 호출은 미노출이다.
- private lease/decoder와 signed query는 오프라인 계약이다. 키 발급·keepalive 요청·인증 연결을 수행하지 않았다.
- private 이벤트도 계정 reconciliation_required=true이며 보호·포지션 동기화 완료를 주장하지 않는다.
- 원본 public payload를 먼저 보존하고 canonical 결과는 별도 transaction에 저장한다. raw만 남는 실패는 가능하며
  유효한 canonical 데이터로 표시하지 않는다. closed bar 수정은 명시적 검토가 필요하다.
- 개발 freshness book 5초/candle interval+5초는 LIVE 위험값이 아니다. 향후 거래 게이트의 독립 검사가 필요하다.
- 단일 개발 API가 migration을 실행한다. 다중 replica 운영의 migration/lease는 별도 단계에서 설계해야 한다.
- 기존 Compose project/volume을 유지했다. 테스트의 downgrade는 임시 schema에서만 수행했다.

## 해결한 문제와 남은 제한

- 복원 후 DB 프로세스가 정지돼 있어 먼저 재기동한 후 DB 검사를 수행했다.
- REST snapshot 출력 시각 때문에 buffered 첫 depth bridge를 잘못 거부하지 않도록 sequence 기준으로 처리했다.
- in-flight 수집 작업과 종료의 경쟁을 테스트해, 진행 중 작업 완료 뒤 상태를 무효화하도록 직렬화했다.
- nonroot 컨테이너에서 Alembic이 pyproject를 읽지 못하는 권한 오류를 COPY 소유권 지정으로 수정했다.
- 실제 공개망은 외부 blocker다. 환경 draft에 기존 문서 도메인을 보존하면서 fapi.binance.com과
  fstream.binance.com 허용 요구를 추가했다. 설정 적용 후 공개 REST/WS 수집을 다시 확인해야 한다.
- 계정 상태 대조, 실제 private 인증·DEMO 체결, 전략/백테스트, 주문 상태 전이·보호 엔진은 후속 범위다.
  T-01~T-18 전체 통과 또는 거래 수익성/실거래 안전성을 주장하지 않는다.
- 다음 단계는 승인 후 **Phase 2 — 룰 기반 전략 + 백테스트**다. 자동 진행하지 않는다.

## 재사용 설정

환경 초안에 Phase 1 startup/migration/수집·검사 지침(`start_skill`)을 저장했고 저장 성공을 확인했다.
기존 frozen 설치 스크립트와 문서 도메인은 유지했다. 공개 데이터 도메인 허용 요구는 별도 저장돼 있으며
사용자는 환경 설정에서 검토·저장 후 게시해야 한다. 이 저장만으로 현재 network 설정이나 게시가 적용됐다고 주장하지 않는다.
