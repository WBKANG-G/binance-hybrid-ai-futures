# Binance Hybrid AI Futures Trading System

현재 **Phase 4 — DEMO 실행·복구 코드와 로컬 검증**을 진행했습니다.
**실제 DEMO 수락 검증은 proxy 403·DEMO 키 부재로 미완료입니다.**
기준은 [MASTER_SPEC_V1_0.md](MASTER_SPEC_V1_0.md), [AGENTS.md](AGENTS.md)입니다.
[LOCKED] 변경 없음, LIVE 차단·DEMO 쓰기 기본 비활성, 미확정 LIVE 위험 숫자 12개는 계속 null입니다.

## DEMO 진행 상태와 재개

전용 transport/키 분리, 영속 위험 예약·UNKNOWN 복구, Algo SL/TP 검증, 계좌 대조·private 감시,
내부 긴급 명령을 구현했습니다. 앱은 DEMO worker를 시작하지 않습니다. 실제 주문·체결 검증은 대기 중입니다.

```bash
uv run --frozen python -m scripts.demo_probe --output .cache/new-demo-probe.json
```

현재 실제 DEMO REST는 proxy CONNECT 403, 공개 WSS도 403입니다. DEMO 키/secret도 실행 환경에 없습니다.
환경 설정에 전용 도메인·direct runtime 변수 요구사항을 저장했습니다. 키 값은 채팅이나 소스에 넣지 마세요.
명시적 preflight/worker/복구 순서는 [재개 지침](docs/phase4-resume.md)을 따릅니다.

[Phase 4 검증 진행 보고](docs/phase4-acceptance.md) · [설계/제한](docs/phase4-architecture.md) · [변경 파일](docs/phase4-files.md)

## 명시적 PAPER 재현

API/앱을 시작해도 PAPER worker는 비활성입니다. 새 계좌와 새 출력 경로를 명시해야 합니다.

```bash
export UV_CACHE_DIR=/workspace/.cache/uv
uv run --frozen alembic upgrade head
uv run --frozen python -m scripts.run_paper --demo \
  --policy configs/paper-sandbox.example.yaml --account PAPER-my-first-run \
  --output .cache/my-paper-run
```

합성 신호 → 위험 예약 → LIMIT 진입 → step으로 내린 약 30% 부분체결 → 손절 검증 → 잔량 취소 →
TP → flat 확인과 SL 정리를 실행합니다. 입력·원장·보고서를 저장하며 기존 계좌/출력은 덮어쓰지 않습니다.
샌드박스 숫자는 LIVE 승인값이 아닙니다. 모든 종목에 합산 상관 위험 cap을 적용합니다.

`--replay input.json`으로 명시적 SIGNAL/FILL/MARK/CANCEL/COMMAND/RECONCILE 이벤트를 재현할 수 있습니다.
`--resume --replay recovery.json`은 같은 정책·기존 PAPER 계좌를 사용하고 만료된 leader lease 이후
재대조합니다. 입력 `as_of`와 event 시각은 가상 UTC clock입니다. lease는 30초이므로 더 긴 중단은
별도 resume으로 복구하며, stale 계좌/시장 입력으로 새 진입을 허용하지 않습니다.
샘플 입력은 생성된 `input.json`, 결과는 `ledger.json`/`report.json`입니다.

[구현·안전 계약](docs/phase3-architecture.md) · [검증 보고](docs/phase3-acceptance.md) · [수정 파일](docs/phase3-files.md)

## 오프라인 연구 실행

Python 3.12+ / uv 0.12.23을 사용합니다. 기본 설정은 실거래용과 별개인 `configs/research.example.yaml`이며
BACKTEST 전용입니다. 연구 규칙은 [사전 고정 프로토콜](docs/phase2-research-protocol.md)을 참고하세요.

```bash
make install
uv run --frozen python -m scripts.run_backtest --demo --folds 2 --test-hours 24 \
  --output .cache/my-research-run
```

데모는 **합성 데이터**입니다. 시장 수익성이나 체결률 검증이 아닙니다. 12일/17,280개 1m 봉과 명시적인
펀딩 ledger로 두 전략·두 시간 구간·두 체결 가정을 실행합니다. 기존 리포트가 있으면 덮어쓰지 않습니다.
결과: `summary.json`, `report.html`, 실행별 거래·equity·후보 CSV, 재사용 가능한 input CSV/JSON.

자체 데이터:

```bash
uv run --frozen python -m scripts.run_backtest --bars data/bars.csv \
  --instrument data/instrument.json --funding data/funding.json \
  --profile configs/research.example.yaml --folds 2 --test-hours 24 \
  --output .cache/my-data-run
```

입력은 단일 symbol의 연속·중복 없는 확정 1m Candle CSV입니다. symbol/interval/open_time/close_time,
open/high/low/close/volume/quote_volume/closed/as_of/received_at 열이 필요합니다. `closed`는 true/false를 명시합니다.
Instrument JSON에는 symbol, listed_at, tick_size, quantity_step, provenance를 제공합니다.
Funding JSON은 covered_from, covered_until, events(at/rate/mark_price)를 명시해야 합니다.
데모 결과의 `input-*` 파일이 실제 형식 예제입니다. 가격/비용은 문자열 Decimal, 시각은 timezone-aware ISO 8601입니다.

## 전략과 체결 가정

- 추세추종: 1h EMA50/200·ADX14, 15m EMA20 되돌림, 5m 돌파, 확인된 pivot/ATR 손절, 연구 목표 2R.
- 돌파: 현재 봉을 제외한 20개 15m 가격/거래량 기준, 5m 실행, 연구 목표 1.5R.
- 확정·가용 시각 기준 5m/15m/1h as-of join, warm-up, 미래 정보 없는 EMA/ATR/ADX와 2봉 우측 확인 pivot.
- NEVER와 TRADE_THROUGH 체결 가정을 분리합니다. touch만으로 체결을 보장하지 않고 시장가 추격은 없습니다.
- 신호 봉 진입 금지, SL/TP 동시 도달 시 SL 우선, 유리한 fill 봉 TP 순서 가정 금지, gap·정산 가격 보수화.
- 양방향 수수료·adverse slippage·펀딩을 포함합니다. 시간 구간마다 과거 warm-up과 hold+TTL embargo가 있습니다.
- profile/data/funding/구현 hash와 가정·제한을 기록합니다. 단일 포지션 연구 모형이며 실제 margin/청산/queue/partial-fill은 없습니다.

연구 후보는 실행 명령·위험 승인이 아닙니다. DEMO 계좌 대조·감시 코드는 별도 CLI에 있으며 실제 외부 검증 대기 상태입니다. AI 모델은 후속 단계입니다.

## 앱과 공개 데이터

```bash
make db
make migrate
# 별도 터미널
make api
make web
```

전체 cloud 실행: `python -m scripts.compose_cloud up -d --build --wait`, 이후 `python -m scripts.smoke`.
Compose는 migration 후 API를 기동합니다. 기존 DB 보존을 위해 project/volume 이름은 hybrid-phase0를 유지합니다.
상태 API는 phase=4이며 orders/LIVE false입니다. 화면에는 거래 잔고·수익률을 만들어 넣지 않습니다.

공개 수집기는 기존 명령을 사용합니다:
`uv run --frozen python -m scripts.collect_market --symbol BTCUSDT --interval 1m --seconds 30`.
현재 재검증에서 fapi.binance.com·fstream.binance.com은 **허용목록에 존재하지만 proxy CONNECT 403**입니다.
실제 WSS도 InvalidProxyStatus 403입니다. 플랫폼 proxy 정책/런타임 적용 상태 확인이 필요하며 다른 호스트로 우회하지 않습니다.
연결이 검증될 때까지 실제 Binance 시장 성과는 주장하지 않습니다. [접근 재검증](docs/phase2-connectivity.md).

## 검증·문서

```bash
TEST_DATABASE_URL=postgresql+psycopg://dev:dev@127.0.0.1:5432/hybrid make check
uv run --frozen alembic check
```

DB 변수를 생략하면 DB 검사는 skip됩니다. 완료 검증에는 실제 DB를 사용합니다.
[Phase 4 진행 보고서](docs/phase4-acceptance.md) · [수정 파일](docs/phase4-files.md) ·
[Runbook](docs/runbook.md) · [API](docs/api.md) · [결정](docs/decisions.md) · [위협모델](docs/threat-model.md).

**Phase 4 실제 수락 검증 대기 중이며 Phase 5는 별도 승인 없이 진행하지 않습니다.**
