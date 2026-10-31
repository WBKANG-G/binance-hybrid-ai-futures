# Binance Hybrid AI Futures Trading System — Codex 개발 마스터 명세서 V1.0

- 문서일: 2026-10-19 (Asia/Seoul)
- 문서 상태: 아키텍처/개발 범위 기준안. 미확정 위험 파라미터는 실제 자금 거래 전 승인 필요.
- 목적: Binance USDⓈ-M Futures의 USDT 무기한 선물 대상, 기술적 전략+AI 평가 기반의 단타·데이 트레이딩 자동매매 시스템 설계 및 단계적 구현
- 우선순위: (1) 주문/포지션 안전성 (2) 데이터 정확성 (3) 검증 가능성 (4) 운용 및 관찰 가능성 (5) 매매 성과
- 거래 성과나 원금 보호는 보장하지 않음. API·상품 이용 가능 지역/계정 자격은 연결 전에 확인.

## 0. Codex 작업의 최상위 규칙 (반드시 준수)

1. 기본 동작은 `LIVE_TRADING_ENABLED=false`이고, 개발·테스트 단계에서 실거래 주문을 절대 보내지 않는다.
2. 위험관리 필수 설정값이 미확정(`null`)이면 PAPER용 명시적 샌드박스 프로필을 제외하고 주문을 허용하지 않는다. 실거래용 프로필에 샌드박스 값을 자동 복사하지 않는다.
3. 위험관리와 포지션 보호가 AI/프론트엔드보다 우선한다. AI 출력은 승인된 위험 한도를 변경할 수 없다.
4. 신규 진입은 **지정가 중심**, 초기 버전에서는 미체결 시 시장가 추격을 금지한다. 긴급 청산은 별도의 `reduceOnly` 시장가 등 청산 전용 정책으로 수행한다.
5. 거래소 주문 요청이 타임아웃됐으면 상태를 `UNKNOWN`으로 기록하고 `clientOrderId` 조회 및 체결/포지션 대조 전 주문을 중복 생성하지 않는다.
6. 진입 부분체결도 실제 체결 수량을 기준으로 즉시 보호한다. 보호 주문 생성·확인 실패 시 추가 진입을 막고 위험 대응을 수행한다.
7. 거래소의 주문/포지션/조건부 주문은 각각 별개의 실체이다. `orderId`, `clientOrderId`, `algoId`, `clientAlgoId`를 구분한다.
8. `float`로 금액/수량을 계산하지 않는다. Python `Decimal`, DB `NUMERIC`, 명시적인 단위·통화·반올림 정책을 사용한다.
9. 외부 입력과 웹훅 메시지를 명령으로 직접 실행하지 않는다. 관리자 API는 인증, CSRF/재인증, 권한, 명령 idempotency, 감사 기록을 사용한다.
10. 개발 단계별 완료 기준을 충족하지 못하면 다음 단계로 진행하지 않는다. 코드를 만들기 전에 테스트를 작성하고, 테스트 실패를 감추지 않는다.
11. 현재 바이낸스 USDⓈ-M 공식 문서를 근거로 구현하되, 각 단계 시작 시 API 변경 로그와 endpoint/필드 유효성을 확인한다. 공식 문서의 예시값을 실제 주문 값으로 사용하지 않는다.
12. 단순 `/fapi/v1/order/test` 통과는 실제 체결/보호 주문 검증이 아니다. PAPER→DEMO→실거래는 별개의 게이트다.

## 1. 요구사항 상태표

### 1.1 사용자가 확정한 요구사항 [LOCKED]
| ID | 항목 | 확정 내용 |
|---|---|---|
| F-01 | 거래소 | Binance Futures |
| F-02 | 시스템 성격 | 하이브리드 AI 자동매매 |
| F-03 | 매매 스타일 | 단타·데이 트레이딩 |
| F-04 | 종목 선정 | 주요 코인 고정 분석 + 전체 시장 자동 탐색의 하이브리드 |
| F-05 | 초기 전략 | 추세추종 + 돌파 매매만 활성화 |
| F-06 | AI 구조 | 단계적 확장형: LightGBM 중심 → 비교 검증 후 XGBoost/LSTM/Transformer |
| F-07 | 리스크 | 사용자 맞춤형; 수치 미확정 |
| F-08 | 신규 진입 실행 | 지정가 중심 (Limit First) |
| F-09 | UI | 기본 모드 + 전문가 모드 |

### 1.2 초안 제안이며 실거래 전 확정해야 하는 값 [OPEN]
| ID | 필드 | 후보/설명 | 기본 운용 |
|---|---|---|---|
| O-01 | 선물 상품 | USDT 정산 무기한(`USDT-PERP`) | PAPER 설정 가능; LIVE 수동 승인 |
| O-02 | 포지션 모드 | One-way, 격리(Isolated), 단일 자산(USDT) | 기존 계정 설정 검사, 임의 변경 금지 |
| O-03 | 고정 분석 대상 | BTCUSDT/ETHUSDT/SOLUSDT 등 | 사용자 변경 가능 |
| O-04 | 자동 선정 | 24h 거래대금 5천만 USDT, 90일 상장, 상위 20개 | 실험값, 코인별 검증 필요 |
| O-05 | 시간봉 | 1h 시장 판단, 15m 신호, 5m 실행 | 실험값, 확정봉 원칙 |
| O-06 | 거래당 위험 | 계좌 평가액 대비 비율 | LIVE 필수 설정 |
| O-07 | 레버리지 상한 | 사용자 지정 (초기 실험 제안 최대 3배) | LIVE 필수 설정 |
| O-08 | 동시 포지션 | 사용자 지정 | LIVE 필수 설정 |
| O-09 | 일/주 손실·MDD | 사용자 지정 한도 | LIVE 필수 설정 |
| O-10 | 명목금액/증거금/상관노출 | 사용자 지정 한도 | LIVE 필수 설정 |
| O-11 | 스프레드/슬리피지 제한 | 사용자 지정 또는 검증값 | LIVE 필수 설정 |
| O-12 | 지정가 대기시간 | 전략/종목별 검증값(예: 5초는 실험값) | 자동 시장가 전환 금지 |
| O-13 | 손절/익절 | ATR·가격 구조·목표 R, 전략별 보유기간 | 규칙 명확화·백테스트 필요 |
| O-14 | 모델 성공 판정 | 최종 독립 검증셋 기준 | 최초 전략 연구 후 정의 |
| O-15 | 재학습 | 후보 모델 매주 생성 | 후보 검증 후 승인 배포 |

원칙: Codex는 `OPEN` 값을 임의로 실거래 기본값으로 결정하지 않는다. 유효성 검증 및 실험용 별도 설정값만 작성한다.

## 2. 기능 범위

### 2.1 P0 (최소 작동·안전)
- Binance 공식 REST/WebSocket 연동, 환경 분리, 서명 및 키 관리
- 과거 시세 수집/정규화/무결성 검사, 실시간 시세 저장
- 단일 계정 단일 실행 리더, 주문 상태 머신, 체결 대조, 포지션 보호/복구
- 단일 코인 지정가 주문 PAPER/DEMO 전 과정
- 독립 위험관리, 긴급 신규 진입 정지/청산, 감사 로그
- 규칙기반 추세·돌파 및 검증용 백테스트
- 기본 웹 대시보드 + 필수 제어

### 2.2 P1 (사용자 확정 시스템 완성)
- 주요 코인+자동 탐색, 코인별 기회 순위 및 상관위험
- LightGBM 신호 평가, 확률 보정, 기대손익, 모델 레지스트리
- 후보 모델 주기적 학습·Walk-forward 검증
- 전문가 모드, 전략/리스크 설정 버전 관리, 알림·통계
- 장애 주입 및 장기 데모 검증

### 2.3 P2 (성능 근거 확보 후)
- XGBoost 비교, LSTM/Transformer 확장, 앙상블
- 더 정밀한 호가·미결제약정 수집 및 피처
- 지정가 재호가·메이커/테이커 전략 개선(초기 비활성화)
- 추적 손절, 분할 익절, 평균회귀 추가(현재 초기 전략 범위 밖)
- 멀티 계정·고가용성 인프라·추가 거래소

### 2.4 비목표
- 수익률 보장, 무제한 레버리지·마틴게일, 무조건 손실 복구, 초저지연 HFT, 과거 데이터로 미래 확정적 예측, 인간 승인 없는 미검증 모델 실거래 배포

## 3. 아키텍처

```text
                         [React + TypeScript UI]
                    Basic Mode | Expert Mode | Controls
                                |
                       HTTPS / WSS + Auth
                                v
                       [FastAPI Control API]
               Query API | Command API | Live Broadcast
                                |
                      [PostgreSQL + Outbox]
                                |
          +---------------------+-----------------------+
          |                     |                       |
          v                     v                       v
  [Market/Data Worker]   [Signal & AI Worker]   [Execution Coordinator]
  WS, REST, OHLCV        Universe, Strategies,   Account single-writer,
  funding, book          regime, ML inference    order state machine
          |                     |                       |
          +---------------------+-----------------------+
                                |
                       [Independent RiskGate]
                       pre-trade + portfolio guard
                                |
                       [Exchange Adapter]
                       REST/Private WS
                                |
                      Binance USDⓈ-M Futures
                                |
                        [Protection Supervisor]
                   SL/TP status, reconcile, emergency
                                |
                     [Monitoring + Notifications]
```

- 처음부터 마이크로서비스 다수로 분할하지 말고 **모듈형 모놀리스 + 격리된 worker/감시 프로세스**로 시작한다. 독립 트레이딩 코디네이터를 계좌당 1개 실행한다.
- API 서버가 재시작되거나 학습 Worker가 실패해도 주문·보호 Worker는 계속 실행한다.
- 큐는 PostgreSQL transactional outbox + durable worker polling을 기본으로 하고, Redis는 선택 캐시/방송 용도로 한정한다. Redis 자체를 진실의 원천으로 사용하지 않는다.
- 중복 실행 보호: 계좌별 lease/fencing token, DB 원자적 상태 전이, 고유 주문/신호 제약. 리더 분쟁 시 신규 주문은 fail-closed.
- 단일 진실 원천: 거래소 주문·잔고·체결/포지션은 거래소, 내부 명령·전략·위험 예약·로그는 DB. 두 소스를 reconciliation으로 대조한다.

## 4. 코드 및 폴더 구조

```text
repo/
  apps/
    web/                       # React + TypeScript + Tailwind
    api/                       # FastAPI 인증/조회/명령
  services/
    market_data/               # historical, websocket, normalization
    universe/                  # fixed + discovery + eligibility
    strategies/                # trend_following, breakout
    ai/                        # features, labels, trainer, evaluator, registry
    portfolio/                 # exposures, correlations, equity, PnL
    risk/                      # sizing, configurable limits, circuit breakers
    execution/                 # limit entry, order state, fills, idempotency
    protection/                # algo SL/TP, monitor, reconcile, flatten
    operations/                # health, notification, audit, recovery
  integrations/
    binance_usdm/              # REST/WS transport, payload mapping
    telegram/
  packages/
    domain/                    # versioned immutable DTO/events/enums
    persistence/               # SQLAlchemy models + Alembic migrations
    observability/             # logging, metrics, tracing
  backtest/                    # point-in-time simulation, costs, walk-forward
  configs/
    config.schema.json         # validation; no secrets
    paper.example.yaml         # example only
    live.example.yaml          # required values null; live disabled
  tests/
    unit/
    integration/
    contract/
    fault_injection/
    e2e/
  infra/
    docker-compose.yml
    docker-compose.demo.yml
    docker-compose.live.yml
  docs/
    api.md
    runbook.md
    threat-model.md
    decisions.md
    model-card-template.md
  AGENTS.md
  README.md
  pyproject.toml
```

주요 기술: Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2+, Alembic, PostgreSQL, pandas/NumPy, LightGBM, scikit-learn, PyTorch(P2), pytest, mypy, Ruff, React+TypeScript, TanStack Query, Zustand, Lightweight Charts, Docker Compose. 버전 범위는 구현 착수 시 호환성을 확인하고 lockfile로 고정.

## 5. 이벤트 계약 및 상태 머신

### 5.1 공통 필드
- `event_id: UUID`, `event_type`, `schema_version`, `environment` (`BACKTEST|PAPER|DEMO|LIVE`), `account_id`, `strategy_id?`, `signal_id?`, `position_id?`, `event_time_utc`, `ingest_time_utc`, `correlation_id`, `causation_id`, `payload`.
- 외부 메시지를 정규화하면서 거래소 원본과 수신 시각을 별도 보관한다. 숫자는 문자열 표현으로 직렬화하여 Decimal로 파싱한다.
- idempotency key는 `(environment, account_id, source, external_event_id)` 기준 유일 제약. 외부 이벤트 ID가 없는 경우 이벤트 유형별 안전한 복합키를 정의한다.

### 5.2 핵심 도메인 계약

```json
{
  "signal_id": "uuid",
  "environment": "PAPER",
  "symbol": "BTCUSDT",
  "strategy": "TREND_FOLLOWING",
  "direction": "LONG",
  "signal_closed_candle_at": "2026-10-19T07:00:00Z",
  "expires_at": "2026-10-19T07:02:00Z",
  "entry_policy": "LIMIT_ONLY",
  "suggested_limit_price": "100000.0",
  "initial_stop_price": "99000.0",
  "target_price": "102000.0",
  "ai_model_version": "lgbm-v1",
  "prob_take_profit": "0.55",
  "prob_stop_loss": "0.35",
  "prob_timeout": "0.10",
  "expected_net_r": "0.20",
  "risk_profile_version": "unassigned"
}
```

샘플 숫자는 가상 예시. 사전 위험 검사 이전 신호에는 `approved=true` 필드를 넣지 않는다. 실제 주문은 승인된 리스크 스냅샷에 의해 별도 생성된다.

### 5.3 주문 상태 머신
`CREATED -> RISK_RESERVED -> SUBMITTING -> ACKNOWLEDGED -> (PARTIALLY_FILLED | FILLED | CANCELED | EXPIRED | REJECTED)`.
- 타임아웃: `SUBMITTING -> UNKNOWN -> RECONCILING -> 확정상태`.
- 취소 요청 후에도 추가 체결 이벤트 가능. `CANCEL_REQUESTED`는 확정 취소가 아니며, 포지션 수량을 체결 기준으로 갱신한다.
- 안전 재호가 초안은 P2까지 OFF. 초기에는 유효기간 만료 시 취소 및 잔량 포기, 이미 체결된 수량은 보호.

### 5.4 포지션 상태 머신
`FLAT -> ENTRY_PENDING -> (PARTIALLY_OPEN|OPEN_UNPROTECTED) -> PROTECTED -> EXIT_PENDING -> CLOSED`.
별도 `RECONCILIATION_REQUIRED`, `PROTECTION_FAILURE`, `EMERGENCY` 플래그.
- `PROTECTED`는 거래소에서 해당 방향의 SL 주문 상태가 유효하고, 실제 체결 수량/전체 포지션에 대한 보호 의미가 검증된 경우만 허용한다.
- 포지션 제어명령 상태는 `ACCEPTED / EXECUTING / VERIFIED / FAILED / UNKNOWN`; 버튼 클릭을 완료로 표시 금지.

## 6. 실시간 데이터·종목 선정

- REST: `/fapi/v1/exchangeInfo`, 24h ticker, kline, bookTicker, mark price, funding, open interest(가능 기간 확인).
- WebSocket: 신규 2026 라우팅 `/public`, `/market`, `/private`의 목적별 경로. 실제 스트림 URL 조립은 공식 문서와 테스트에서 고정.
- 각 시세 데이터에는 거래소 이벤트 시각, 수신 시각, symbol, interval, 봉 확정 여부를 저장한다.
- AI와 전략은 **완성된 봉만** 사용. 1h/15m/5m을 신호 시점에 이전에 확정된 봉끼리 as-of 정렬한다.
- 결측/이상치/역전 시간/중복 캔들 발견 시 신규 진입 보류 및 데이터 정합성 이벤트 생성.
- `UniverseSelector`: 고정 주요 코인 + 동적 후보(상품 상태 TRADING, USDT perpetual, 충분한 상장 기간/거래량/스프레드/호가, 예외 목록 제거); 매시간 갱신은 초기 실험값.
- 보유 포지션의 코인이 후보 순위에서 제거되어도 보호/청산 관리는 유지하고, 신규 진입만 제한한다.
- 회고 백테스트에서 미래 상장 종목이나 미래의 거래대금 랭킹을 사용하지 않는다.

## 7. 전략 규칙 (정량화 필요 항목 포함)

### 7.1 추세추종 (초기 연구 가설)
- 1h 확정봉 EMA50 > EMA200 (LONG), 반대는 SHORT; ADX14>=23.
- 15m EMA20 되돌림 후 방향 재확인, 5m 확정봉 돌파로 후보 발생.
- 손절은 사전에 확정된 피벗과 ATR(14) 기반. 피벗은 오른쪽 2개 봉이 완료된 뒤에만 유효.
- 익절 후보 2.0R, 최대 보유 4h (실험값).
- 구현 전 다음 매개변수의 수식을 **고정**한다: EMA 근처의 최대 거리, 되돌림 유효기간, 5m 고점/저점 신호 시점, ATR 유효 범위, 연속 신호 중복 억제, 거래비용 제외 기준.

### 7.2 돌파 (초기 연구 가설)
- 직전 확정 20개 15m 봉(현재 신호봉 제외) 최고/최저 돌파; 신호봉 종가 확인.
- 거래량 >= 직전 20개 확정봉 평균의 1.5배(현재 봉 제외), 변동성/스프레드 허용.
- 익절 후보 1.5R, 최대 보유 2h (실험값).
- 지정가 주문이 체결되지 못하는 경우를 백테스트에 반영한다. 호가/체결 자료가 없으면 체결률을 추정하지 말고 보수적 시나리오 범위를 제시한다.

### 7.3 모든 전략 공통
- 신규 진입 전 `MarketRegimeGate`, `AITradeEvaluator`, `RiskGate`, `DataFreshnessGate`, `ExecutionCostGate` 통과 필수.
- 한 봉 내 고가·저가 동시 터치 등 체결 순서 불명확성은 보수적으로 처리하거나 더 세밀한 데이터로 해소. 동일 봉에서 진입/손절/익절이 겹칠 때 낙관적 결과 금지.
- 기대손익은 익절·손절·시간종료 결과와 총 거래비용(수수료·슬리피지·펀딩비)을 반영.

## 8. AI 모델 / 학습 / 평가

### 8.1 P1 모델
- `MarketRegimeClassifier`: 규칙 기반 baseline을 먼저 구축; 추세·변동성·돌파 신호를 내부 분리된 변수로 표현.
- `TradePredictionModel`: LightGBM 분류기. 거래 후보마다 TP-first/SL-first/TIMEOUT 중 결과 확률 예측.
- `ProbabilityCalibrator`: 학습/보정/평가 데이터 독립(시계열 순서 준수). 확률 0~1, 합≈1, 불확실성 검사.
- `ExpectedValue`: 실제 진입 후보, 예상 체결, 손절, TP, 최대 보유시간과 수수료·슬리피지에 일치하는 순기대손익 계산.
- 모델이 출력한 확률로 실거래 손절 레벨이나 위험 상한을 임의로 옮기지 않는다.

### 8.2 학습 표본과 라벨링
- 5m/15m/1h 확정 캔들 피처; 후보가 생성된 바로 그 시점에 볼 수 있었던 입력만 저장.
- Triple-barrier 형태: 기준 진입가/손절/목표/시간 경계. TP, SL, TIMEOUT (동시 터치 불확실성 별도 flag).
- 지표 계산 warm-up 기간 확보. `target_label_end_time`을 기록하고 검증 구간을 침범하는 학습 샘플을 purging, 필요한 embargo.
- 최소 2년~가능하면 3년 데이터 사용은 연구 목표일 뿐, 실제 입수 가능성/시장 환경을 확인한다.
- train/validation/test는 시간순. 검증셋 튜닝 후 최종 테스트셋 재사용 금지. 순차 Walk-forward 사용.
- 코인 선정/피처정규화/확률 보정/하이퍼파라미터 선택 모두 각 폴드의 학습 구간 데이터만 사용.
- 비교군: AI 없는 규칙기반 전략, 무작위/기준 예측, LightGBM, 후속 XGBoost.

### 8.3 성능 게이트
- 주요 지표: 거래당 순 기대 R, MDD, PF, 체결률, 총수수료, 분기별 성능, 종목·롱숏·시장 상태별 안정성, 모델 확률 보정(Brier score/ECE).
- 특정 숫자를 검증 전 수익 보장 기준으로 고정하지 않는다. 전략 탐색 전 미리 평가 기준과 검증 구간을 등록·동결한다.
- 학습된 모델은 `CANDIDATE -> VALIDATED -> PAPER/SHADOW -> APPROVED -> DEPLOYED -> ROLLED_BACK` 상태 관리.
- 후보 모델 자동 생성과 실거래 모델 자동 교체 분리. 초기 실거래 배포는 재인증을 통한 사용자 승인 필요.
- drift 감지 시 거래를 확장하지 않고 보수적으로 모델 비활성화/HOLD 가능.

## 9. 사용자 맞춤형 위험 관리 (LIVE 필수값)

### 9.1 필수 파라미터
- `risk_per_trade_pct`, `max_leverage`, `max_concurrent_positions`, `max_single_notional_pct`, `max_total_notional_pct`, `max_margin_usage_pct`, `daily_loss_limit_pct`, `weekly_loss_limit_pct`, `max_drawdown_pct`, `max_correlated_risk_pct`, `max_slippage_pct`, `max_spread_pct`.
- `allow_averaging_down=false`, `allow_martingale=false`, `protective_stop_required=true`는 초기 버전에서 사용자 해제 불가.
- 보호주문 실패, 시간동기 실패, 가격 데이터 지연, 상태 대조 실패 시 신규 진입 `FAIL_CLOSED`.

### 9.2 수량 계산
- 기본 계좌 위험 예산 `B = Equity_USDT * risk_per_trade_pct / 100`.
- USDT 선형선물 롱/숏 공통 예상 손절손실(단위당) `abs(expected_fill_price - estimated_stop_fill_price) + unit_fees + unit_funding_allowance`.
- `qty_raw = B / unit_loss`; 거래소 스텝으로 내림한 뒤 예상 비용/증거금/노출/상관/최소 수량 재검사.
- 실제 체결가와 슬리피지, 예상 손절 체결가로 포지션 위험을 다시 계산하고 초과 시 축소·중지.
- 사용자 정의 손실한도는 계획상 위험 한도이며, 갭/슬리피지/강제청산에 의해 초과할 수 있음을 UI 및 로그에 명시.

### 9.3 포트폴리오 위험
- 실제 포지션 위험 + 미체결 진입 주문의 예약 위험을 합산.
- 계좌 단위 동시 신호는 DB 트랜잭션으로 위험 예약을 원자 처리; 중복 계좌 실행 방지.
- BTC/ETH 등의 동시 포지션은 상관·공통 급변 위험을 평가; 상관관계는 종목·기간별 stale 검사.
- 손절이 없는 포지션/보호 미확인 포지션의 노출은 보수적으로 최대 위험 처리.

### 9.4 위험 중단
- 상태: `NORMAL`, `COOLDOWN`, `DAILY_LOCK`, `WEEKLY_LOCK`, `DRAWDOWN_LOCK`, `UNHEALTHY`, `EMERGENCY`.
- 손실 기준은 외부 입출금을 조정한 계좌 평가액으로 계산하고 실현/미실현/수수료/펀딩을 포함한다.
- 거래일 경계는 KST 00:00, 내부 타임스탬프는 UTC. 주간 시작일은 UI/설정에서 고정.
- 재개는 장애 원인 해소 및 모든 위험 게이트 재통과 후 가능. 일부 하드락은 관리자 재인증 필요.

## 10. 지정가 실행 및 보호 주문

### 10.1 신규 진입 — 지정가 전용 P0/P1
1. 확정봉 신호+모델 예측 유효성, 시장 데이터 freshness, 위험 예산 예약.
2. 최신 주문가능 호가 확인; tickSize/stepSize/notional/가격 제한, 예상 수수료 및 스프레드 검사.
3. `LIMIT` 신규 주문(`GTC` 기본 후보, `GTX` post-only는 검증 후 선택). 가격·수량은 Decimal quantize.
4. DB에 의도/ID/예약을 먼저 기록한 후 거래소에 제출. 주문 요청 결과가 불명확하면 `UNKNOWN`.
5. 유효시간까지 ACK·체결·부분체결·취소 이벤트 감시. 일부 체결되면 체결수량에 대한 보호 수행.
6. 미체결 시간 초과 시 잔여 수량 취소 요청→취소 상태/추가 체결 조회→남은 주문 위험 예약 정리.
7. 초기에 **미체결 신호를 시장가로 변경하는 자동 전환 금지**. 별도 새 신호만 신규 진입 후보로 인정.

### 10.2 거래소 측 손절/익절
- Algo order `POST /fapi/v1/algoOrder`, `algoType=CONDITIONAL`, `STOP_MARKET`, `TAKE_PROFIT_MARKET` 사용.
- One-way 전량 청산 검증 기본안: `closePosition=true`, 방향은 기존 포지션 반대, `quantity`/`reduceOnly`와 동시 송신 불가.
- Algo order 식별자는 일반 order 식별자와 완전히 분리한다. `GET /fapi/v1/algoOrder`, `GET /fapi/v1/openAlgoOrders`로 상태 대조.
- 보호주문이 제출된 것만으로 보호 완료 처리 금지; 상태, 방향, 가격, 남은 포지션을 확인.
- `MARK_PRICE` vs `CONTRACT_PRICE`, `priceProtect` 설정은 실제 트리거 방식을 문서화하고 데모 검증. `priceProtect`가 스톱 트리거를 지연시킬 수 있다는 점 주의.
- TP/SL은 자동 OCO가 아닐 수 있으므로, 포지션이 실제 종료된 후 남은 주문을 정리한다. 보호 손절을 먼저 없애고 청산 주문부터 시도하지 않는다.
- 포지션이 열려 있는 동안 하나의 손절이 존재한다는 사실만으로 충분하지 않다. 올바른 포지션/방향/조건/상태인지 검증.

### 10.3 긴급 명령
- `PAUSE_ENTRIES`: 즉시 신규진입 DB 게이트 잠금, 신규 진입 미체결 잔량 취소; 보호 주문 보존.
- `SAFE_STOP`: 신규 진입 차단; 포지션/보호 관리 지속.
- `EMERGENCY_FLATTEN`: 신규 진입 차단, 진입 주문 취소, 거래소 실제 수량 확인, 청산 전용 시장가 등으로 포지션 감소 시도; 실제 `positionAmt=0` 확인 전 성공 표시 금지.
- API 오류로 청산이 불명확한 경우 `UNKNOWN` 및 긴급 경고. 이미 요청한 청산을 무조건 반복하지 않는다.

## 11. UI: 기본 모드 + 전문가 모드

### 11.1 공통
- 모바일 반응형/한국어/KST UI, 저장은 UTC. `BACKTEST|PAPER|DEMO|LIVE` 강한 시각 구분. LIVE는 기본 비활성화.
- 변경된 버튼은 UI 상태가 아니라 서버가 확인한 실행상태만 표시한다. 모든 값에 `as_of`, `is_stale` 제공.
- Frontend는 바이낸스 API 키·서명을 알지 못한다.

### 11.2 기본 모드
- 평가액/가용증거금/당일 순손익/현재 위험상태/AI 모델 상태
- 현재 포지션/손절 보호 유효성/미체결 신규 주문/시스템 연결
- 신규 진입 일시중지/재개/안전정지/긴급청산(추가 확인 포함)
- 진입 후보와 거부 사유 단순 카드, 최근 거래 이력

### 11.3 전문가 모드
- 5m/15m/1h 차트, EMA/ADX/ATR/RSI, 신호 발생과 실제 체결 구분
- 종목 유니버스/AI 평가/P(TP·SL·TIMEOUT)/순기대 R/주요 입력 기여도
- 전략별 파라미터 편집 초안·검증·버전 승인, 코인 예외 목록
- 위험프로필 생성/비교/검증/승인/이력/롤백
- 포지션·주문·Algo 주문·체결·펀딩 및 부분체결 상세
- 계좌/코인/전략/롱숏별 PF/MDD/승률/체결률/거래비용/시계열 그래프
- 후보 모델 학습/Walk-forward/모델 비교/등록/배포·롤백
- 시스템 로그/명령·감사/웹소켓/데이터 정합성/장애 이력

### 11.4 관리 API (초안)
```text
GET  /api/v1/dashboard/summary
GET  /api/v1/account
GET  /api/v1/positions
GET  /api/v1/orders?kind=ENTRY|EXIT|ALGO
GET  /api/v1/signals
GET  /api/v1/universe/candidates
GET  /api/v1/models
GET  /api/v1/risk/config
POST /api/v1/risk/config/validate
POST /api/v1/risk/config/activate
POST /api/v1/control/pause
POST /api/v1/control/resume
POST /api/v1/control/safe-stop
POST /api/v1/control/emergency-flatten
POST /api/v1/positions/{id}/close
GET  /api/v1/operations/{operation_id}
GET  /api/v1/system/health
WS   /ws/v1/stream
```
- 제어 POST에는 인증, 관리자 권한, 중요 작업 재인증, `Idempotency-Key`, `expected_state_version`, CSRF 방어 및 감사 기록 필수.
- 응답 예: HTTP 202 `operation_id`, `accepted_at`, `state=ACCEPTED`; 실제 완료는 operations 조회/서버 이벤트에서 `VERIFIED`.

## 12. 데이터베이스 최소 스키마

| 테이블 | 핵심 키/필드 | 제약 |
|---|---|---|
| `accounts` | id, environment, mode, exchange_account_ref | 민감키 참조만 저장 |
| `candles` | symbol, interval, open_time, OHLCV, is_closed | (source,symbol,interval,open_time) unique |
| `universe_snapshots` | as_of, symbols, features, eligibility | 미래 데이터 사용 방지 |
| `signals` | signal_id, features_hash, expires_at, strategy_version, model_version | idempotent |
| `predictions` | signal_id, p_tp, p_sl, p_timeout, net_ev_r, prediction_time | 불변 기록 |
| `risk_profiles` | version, config_json, validated, approver, activated_at | append-only 버전 |
| `risk_reservations` | reservation_id, signal_id, allocated_risk, state | 원자적 갱신 |
| `orders` | client_order_id, order_id, signal_id, status, qty | 계정/환경 고유 |
| `algo_orders` | client_algo_id, algo_id, position_id, type, trigger, status | 일반 주문과 분리 |
| `fills` | trade_id, order_id, price, qty, fee, realized_pnl | 거래소 체결 고유 키 |
| `positions` | position_id, symbol, side, qty, avg_entry, protection_state | 거래소 대조 |
| `equity_snapshots` | account_id, as_of, equity, margin, available | 입출금 조정 |
| `model_registry` | model_version, features_hash, training_window, metrics, state | 재현 가능 |
| `commands` | operation_id, key, requester, state, exchange_verification | 감사 |
| `outbox_events` | id, topic, payload, delivered_at | 트랜잭션 보장 |
| `audit_logs` | actor, action, prev/new refs, request_id, time | 삭제 방지 |
| `system_incidents` | code, severity, start/end, actions | 장애 분석 |

모든 주문/체결 이벤트 원본을 보존하고 정규화 자료는 별도 갱신한다. 거래소가 과거 기록 조회기간을 제한할 수 있으므로 장기 보관은 내부 DB/백업이 담당한다.

## 13. 보안·운영·배포

- Python/API 통신 및 관리 UI 모두 HTTPS/WSS, 세션·MFA, 권한 분리, 로그 비밀정보 마스킹.
- Binance 거래 API 키는 서버 Secret 저장소/암호화 환경변수로 주입, 출금 권한 금지, IP 화이트리스트 권장.
- `PAPER`, `DEMO`, `LIVE` 서로 다른 환경 설정/시크릿/DB namespace/화면 표시; LIVE 키로 테스트 금지.
- 트레이딩 엔진 1개만 실제 주문 실행 가능; API 서비스/AI Worker가 직접 Binance POST 호출 금지.
- 오토 재시작 시 시작은 `RECOVERING`; 모든 포지션·일반주문·Algo주문·잔고 재대조 전 RUNNING 금지.
- 로그/메트릭: 유저 명령, stale WS, API error, price slippage, protection missing, unconfirmed fill, daily loss, model inference errors.
- 일별 DB 백업·복구 시험, 오래된 데이터 정리 및 감사 로그 보존 정책.
- 모니터링 실패는 신규 진입 제한; 원격 통보 실패는 이벤트 큐에 재전송 기록.
- P0 배포는 개인 PC 개발/리뷰 + Linux VPS Docker 운영 가능 구조. 모델 재학습 작업은 트레이딩 worker와 격리.

## 14. 단계별 구현 계획 / 수락 테스트

### Phase 0 — 저장소 및 요구사항 고정
**산출물**: README, AGENTS.md, docs/decisions.md, 도메인 이벤트/유효성 스키마, 위협모델, 린트·타입검사·테스트 CI.
**수락 조건**: 설치/로컬 실행 명령 재현; LIVE 기본 비활성화, secrets 미포함, 미확정 위험값으로 LIVE 시작 불가.

### Phase 1 — Binance 어댑터/시장데이터
**산출물**: 공식 endpoint 클라이언트, REST 서명, public/market/private WS, 캔들·호가 수집, 재연결, DB migration.
**수락 조건**: 공식 주문 파라미터 계약 테스트; tick/step/price 필터 통과; 연결 끊김·중복/역순/결측 이벤트 테스트; `as_of`와 freshness 확인.

### Phase 2 — 룰 기반 전략 + 백테스트
**산출물**: 1h/15m/5m 정렬, 추세추종/돌파, 거래 후보·라벨, 입출금/비용 반영 백테스트, 리포트.
**수락 조건**: 미래 정보 없음, 현봉 포함 오류 없음, 불확실한 체결/손절 처리 보수적, 거래비용과 종목 상장시점 반영.

### Phase 3 — 위험관리 + 지정가 PAPER 거래
**산출물**: risk gate, sizing, reservation, order/position state machines, PAPER exchange simulator, 우선적인 손절 보호.
**수락 조건**: 같은 신호 중복 주문 0건; 동시 주문 시 예산 초과 0건; 부분체결/취소 타임아웃/손절 실패 주입 테스트 PASS.

### Phase 4 — 실제 바이낸스 DEMO 체결/복구
**산출물**: DEMO 주문, algo SL/TP, 계좌 동기화, 장애 복구, 키 분리.
**수락 조건**: DEMO 환경에서 정상·부분·미체결 주문, 일반·Algo 주문 조회, WS 단절, 서버 재시작, 장애 복구, 비상 정지 시나리오 통과. 거래소가 지원하지 않는 DEMO 기능은 안전한 에뮬레이션 및 제한 사항을 별도 기록.

### Phase 5 — AI P1
**산출물**: feature pipeline, triple-barrier 라벨, LightGBM, 확률 보정, EV 계산, Walk-forward 검증, 모델 레지스트리.
**수락 조건**: 학습 완전 재현, 검증 누출 테스트, 모델 미준비 시 HOLD, AI 없는 전략 대비 독립 구간 비교, 후보 모델 자동 실거래 배포 금지.

### Phase 6 — 주요 코인 + 자동 탐색, 포트폴리오 위험
**산출물**: 동적 유니버스, ranking, 종목별 필터, 포트폴리오 위험/상관 위험, 다중 코인 백테스트.
**수락 조건**: 후보 순위 변경이 기존 포지션 보호를 중단하지 않음; 동시 다종목 신호가 위험 예산을 초과하지 않음.

### Phase 7 — 기본/전문가 UI
**산출물**: 실시간 계좌·포지션·AI·거래내역, 설정검증/버전/승인, 긴급 제어, 알림, 모바일 반응형.
**수락 조건**: 대시보드 상태와 DEMO 거래소 실제 상태 일치; 중요한 명령은 사용자의 클릭이 아닌 거래소 결과 검증 후 완료 표시; 보안 E2E 검증.

### Phase 8 — 강건성/최종 검증/운영 승인
**산출물**: 장기 paper/demo 운영 보고서, 장애 주입 보고서, 보안 리뷰, 배포/롤백 Runbook, LIVE 게이트.
**수락 조건**: 사용자 필수 위험값 확정+승인, 계정 거래 가능성 확인, 위협모델 보완, 계획된 성능 게이트 및 운영 안정성 게이트 통과, LIVE 별도 승인. 미통과면 LIVE 금지.

### Phase 9 — 선택적 연구 확장 (실거래 자동 반영 금지)
- XGBoost, LSTM/Transformer, 앙상블, 고급 지정가 정책, 평균회귀는 독립 브랜치/샌드박스에서 구현하고 Phase 8의 배포 게이트를 다시 통과해야 활성화한다.

## 15. 필수 장애 주입 테스트 명세

| ID | 상황 | 기대 결과 |
|---|---|---|
| T-01 | 주문 POST 응답 유실/timeout | 동일 의도 중복 실행 없음; UNKNOWN→조회→확정 |
| T-02 | 같은 WebSocket fill 2번 수신 | 체결 수량 한 번만 증가 |
| T-03 | 역순 fill/취소 이벤트 | 거래소 최신 상태로 최종 대조, 잘못된 환원 없음 |
| T-04 | 지정가 30% 부분체결 후 취소 | 체결 30% 보호, 잔량만 취소, 예산 정확 정리 |
| T-05 | Stop Algo 생성 실패 | 신규 진입 차단, 위험 대응 및 긴급 경고 |
| T-06 | TP 체결, SL 잔존 | 실제 포지션 0 확인 후 잔여 SL 취소 |
| T-07 | 서버 재시작 중 진입 주문 존재 | 재대조 이전 신규 주문 금지, 중복 진입 없음 |
| T-08 | 웹 UI 이중 클릭 | 동일 operation_id / 1회 실행 |
| T-09 | 계좌 하락이 손실 제한 초과 | 미실현 손실 포함 신규 진입 차단 |
| T-10 | DB 정지/리더 두 개 | fail closed / single-writer 보장 |
| T-11 | AI 추론 실패/NaN | HOLD; 기존 SL/TP 보호 지속 |
| T-12 | LIVE 토글만 변경, 위험값 null | 실거래 시작 실패 |
| T-13 | 교차 종목 동시 위험 예약 | 합산 노출/마진/상관 한도 초과 없음 |
| T-14 | WebSocket stale/재연결 | 신규 진입 중단, REST reconciliation 이후 재개 |
| T-15 | 청산 주문 ACK 뒤 실제 미체결 | 완료 표시 금지, 위험 상태 지속 |
| T-16 | 구식 REST 조건부 주문 호출 | 계약 테스트에서 실패; 최신 Algo endpoint 사용 |
| T-17 | 사용자 외부 수동 포지션 변경 | 변화 탐지 및 확인 전 신규 진입 금지 |
| T-18 | demo 키와 live 환경 혼합 | 애플리케이션 시작 실패 |

## 16. 실거래 승인 게이트 (모두 충족 필요)

1. LIVE 전용 필수 위험 설정값/비용/계정/모드/거래 가능성 확정 및 승인.
2. 실거래 사용 권한·약관·거주 국가 관련 제한 검토 완료.
3. 백테스트 point-in-time, 누출 방지, 거래비용/체결모델 보수적 검증 완료.
4. 사전 동결한 평가 기준으로 미사용 미래 구간(아직 학습하지 않은 과거 분리 구간) 성능 검증 완료.
5. PAPER와 DEMO에서 지정가/Algo 보호/부분체결/청산/장애 복구가 반복 검증됨.
6. 장애 주입 테스트 `T-01`~`T-18` 및 인증·키 관리 검증 통과.
7. 긴급 SAFE_STOP / FLATTEN 기능과 수동 재개 절차 검증.
8. 데이터/로그/모델/DB 백업·복구 훈련 완료.
9. 명시적 사용자 재인증 및 LIVE 활성화 승인. 처음에는 작은 자금으로 제한된 단계적 전환 권장.
10. 위험 게이트 위반이 확인되면 LIVE 자동 비활성화, 재검증 전 신규 진입 금지.

## 17. Codex 실행 원칙 / 작업 출력 형식

각 단계마다 Codex는 다음을 제공한다.
1. 구현 전 해석: 요구사항, 의존성, 미확정 사항, 보안 위험, 수정 파일 계획.
2. 구현 코드: 타입 지정, 에러 처리, 안전한 DB 트랜잭션, 버전별 migration.
3. 테스트: unit/integration/contract/fault injection (단계에 필요한 범위).
4. 사용법: 설치, 설정 파일 예시, 데모 실행 방법, 트러블슈팅.
5. 검증 증거: 실행 명령과 테스트 결과; 실행하지 못했다면 미실행 사유.
6. 변경 요약: 신규 API/스키마/환경변수, 알려진 제한, 다음 단계 게이트.

**절대 안 됨**: 실제 키 생성·요구, LIVE 자동 활성화, 실패 테스트 삭제, 주문 POST 무조건 재시도, 가상의 손절 체결을 실거래 체결로 표시, 미래 구간 데이터를 피처에 주입, AI 모델의 자동 위험한도 증액.

## 18. 참고 공식 문서 (구현 시 최신 변경 로그 재확인)

- Binance USDⓈ-M REST Trade Reference: https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade
- Binance USDⓈ-M Market Data Reference: https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data
- Binance USDⓈ-M WebSocket connection: https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Connect
- Binance 2026-03-06 WebSocket split announcement: https://www.binance.com/en-IN/support/announcement/detail/ebf9b0aa9eca4ff3804eef6fb09ba32a
- Binance developer portal / change log: https://developers.binance.com/

---

**문서 관리 원칙**: [LOCKED] 항목 변경은 사용자 명시 승인 필요. [OPEN] 항목은 별도 `docs/decisions.md`에서 사용자 확정과 검증 결과를 축적. 본 문서만으로 무인 실거래를 시작하는 것은 금지.
