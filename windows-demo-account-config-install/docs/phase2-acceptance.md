# Phase 2 — 전략 연구·백테스트 검증 보고서

검증일: 2026-10-19 (Asia/Seoul).
**Phase 2 구현·오프라인/로컬 수락 검사 완료. 실제 Binance 공개망은 재검증 실패(proxy 403). Phase 3 미착수.**

## 접근 재검증 결과

환경 설정에 fapi.binance.com과 fstream.binance.com 허용 항목이 이미 있었다.
공식 Trade/변경 로그는 HTTP 200으로 재조회했다. 실제 REST time GET은 proxy CONNECT 403,
websockets 17.2로 시도한 실제 public WSS 연결은 InvalidProxyStatus 403이었다.
허용 항목이 있다는 사실로 런타임 접근이 적용됐다고 판단하지 않았다. [진단 기록](phase2-connectivity.md).
플랫폼 proxy 정책/런타임 적용 상태 확인이 필요하다. 중복 allowlist 추가·wildcard 확대·proxy/TLS 우회는 하지 않았다.

## 구현 및 수정 파일

[전체 Phase 2 파일 목록](phase2-files.md).

- `services/strategies`: Decimal EMA/ATR/ADX, 오른쪽 확인 pivot, 추세추종·돌파 연구 후보.
- `backtest`: BACKTEST-only profile/instrument/funding/후보/결과 계약, 1m 검증·다중 시간봉 as-of,
  보수적 체결·비용·펀딩 engine, chronological evaluation, JSON/HTML/CSV audit report, local IO·합성 fixture.
- `scripts/run_backtest.py`, `configs/research.example.yaml`, research schema: 명시적인 연구 CLI와 고정 가정.
- API/web/smoke: phase=2 상태 표시. 거래 잔고·시장 수익률·위험 활성화 기능을 추가하지 않았다.
- CI/Makefile/typecheck/lockfile/Docker: 연구 모듈·검사·패키지 version 0.3.0 포함. 새 외부 라이브러리는 필요하지 않았다.
- README·Runbook·설계·위협모델·연구 프로토콜·본 문서 갱신.

명세/AGENTS/CODEX_START_HERE 변경 없음. 기존 변경을 보존했으며 commit/push/reset을 수행하지 않았다.
사전 연구 규칙은 [phase2-research-protocol.md](phase2-research-protocol.md)에서 확인한다.

## Phase 2 수락 기준

| 기준 | 구현/검증 근거 |
|---|---|
| 미래 정보 없음 | 확정·가용 시각 기준 5m/15m/1h join. actual LONG/SHORT 후보에 미래 bars 추가·변조해도 동일한 결과/ID. captured receipt 지연 검사 |
| 현봉 포함 오류 없음 | 15m breakout과 평균 volume, 5m execution 기준에서 현재 봉 제외. 비교에 현재 봉을 포함하면 실패하는 positive fixture로 검증 |
| warm-up/피벗 | EMA SMA seed·Wilder ATR/ADX golden values, warm-up HOLD, pivot의 오른쪽 2개가 닫히기 전 사용 금지 |
| 불확실한 체결/손절 보수화 | 신호 봉 진입 금지, touch 체결 금지, NEVER/TRADE_THROUGH 분리, SL 우선, fill 봉 TP 미확정, gap stop, timeout/EOD 목표 초과 이익 방지 |
| 비용 반영 | 양방향 fee·adverse exit slippage·funding. funding coverage 부족 거부, 미확정 entry ordering의 유리한 funding 수취 미인정, 손익 이중 차감 없음 |
| 상장 시점/데이터 품질 | listed_at 필수, 상장 전·중복·누락·역순·forming bar 거부. 고정 단일 종목 연구이며 미래 universe ranking을 사용하지 않음 |
| 순차 검증 | 과거 warm-up prefix→최대 hold+TTL embargo→test. test 밖 신호/포지션을 넘기지 않고 말단 정산. 최적화/재학습 없음 |
| 재현/보고 | profile/data/funding/구현 hash, 원본 재사용 입력·후보·거래·equity·가정 포함. 서로 다른 output 두 개의 전체 artifact가 byte-for-byte 동일 |

## 실제 실행한 검사

작업 디렉터리: `/workspace/binance-hybrid-ai-futures`.

| 명령/검사 | 실제 결과 |
|---|---|
| `uv lock`, `uv sync --frozen` | 고정 의존성 설치/프로젝트 version 갱신 성공 |
| `TEST_DATABASE_URL=postgresql+psycopg://dev:dev@127.0.0.1:5432/hybrid make check` | 전체 성공. uv cache도 workspace 경로로 지정 |
| `uv run --frozen pytest` | **151 passed, 0 failed, 0 skipped**. 이전 단계·실제 PostgreSQL·localhost WS·오프라인 CLI 포함 |
| Ruff lint/format | 통과 |
| `uv run --frozen mypy` | 통과, 41 source files |
| frontend lint/typecheck/build | 통과 |
| frontend Vitest | **5 passed** |
| `uv run --frozen alembic check` | drift 없음. Phase 2에 추가 DB schema는 필요하지 않음 |
| `python -m scripts.run_backtest --demo --folds 2 --test-hours 24 --output .cache/phase2-verified` | SYNTHETIC 17,280개 1m bars, 8 runs, 48 candidate evaluations, 12 simulated fills |
| 동일 명령, output `.cache/phase2-reproduced` | 재실행 성공. 모든 output 파일 이름·내용이 byte-for-byte 동일 |
| `python -m scripts.compose_cloud up -d --build --wait` | DB/API/web healthy, 최종 이미지 빌드 성공 |
| smoke 및 API/web restart 후 smoke | phase=2, DB revision, web proxy, market-data status 계약 통과 |
| 네트워크 분리 최종 API 이미지의 LIVE/true 기동 | exit 3, LIVE 미확정 위험값 거부. 주문 요청 없음 |
| `git diff --check`, 기준 문서 diff | 통과 |

리포트는 `.cache/phase2-verified/report.html`, 원시 요약은 같은 디렉터리의 `summary.json`이다.
48은 시나리오별 후보 평가 합계이며 실제 시장 신호 수/체결률 추정이 아니다. 12회는 합성 모형의 체결이다.
합성 데이터에서 한 전략이 후보를 만들지 않은 구간도 그대로 0으로 보고한다. 실제 LONG/SHORT 두 전략의 positive fixture는 별도 검증했다.

## 발견·해결한 문제

추가 adverse-sequencing 검사에서 EOD/timeout 정산이 기존 목표보다 유리한 open/close 가격을 선택할 수 있음을
실패 테스트로 확인했다. LONG은 target 이하, SHORT는 target 이상으로 보수적으로 제한하고 regression test를 유지했다.
슬리피지는 실제 exit price에 한 번만 반영하고 별도 cost를 다시 빼지 않음을 정확한 Decimal 값으로 검증했다.
실제로 공개망이 열리지 않았으므로 문제를 샘플/localhost 성공으로 덮지 않고 외부 blocker로 분리했다.

## 안전성 및 제한

- LIVE/DEMO 시작과 모든 submit은 차단된 상태다. LIVE 위험 숫자 12개 null, AI/웹 위험 변경 API 없음.
- 연구 profile은 Literal BACKTEST이고 실험 숫자를 LIVE에 복사하지 않는다. 연구 후보는 위험 승인/실행 주문이 아니다.
- Phase 3용 실시간 PAPER 엔진·계좌 리더·실제 보호 주문/계정 대조는 아직 구현하지 않았다.
- 현재 성과 리포트는 SYNTHETIC이다. 실제 Binance 시세·계정·체결이나 시장 수익성을 검증하지 않았다.
- historical_close는 가용성 가정이며 과거 수정 이력/실제 latency 복원이 아니다. metadata/listing/funding coverage는 입력 선언에 의존한다.
- 고정 symbol·정적 tick/step, 단일 position·cash 이하 명목금액의 모형이다. queue/partial fill/margin/청산/동적 fee·필터는 미모형화다.
- 두 체결 시나리오는 확률 추정이나 전체 PnL의 보장된 상·하한이 아니다. MDD는 1m close 기준이며 실제 intrabar risk가 더 클 수 있다.
- GitHub 호스팅 CI·실제 브라우저 E2E·환경 게시/새 task 복원은 이번 검사로 입증하지 않는다.
- 다음 단계는 별도 승인 후 Phase 3 — 위험관리 + 지정가 PAPER 거래다. 자동 진행하지 않는다.

## 재사용 환경 지침

Phase 2 설치·앱 기동·오프라인 연구·검사·외부 연결 제한을 담은 start_skill 초안 저장 성공을 확인했다.
기존 frozen install_script와 network allowlist는 유지했다. 새 시작 지침은 환경 설정에서 검토·저장 후 게시할 수 있다.
초안 저장은 실행 환경 network 변경·게시·새 task 복원 성공의 증거가 아니며, 이미 관찰된 proxy 403 해결을 보장하지 않는다.
