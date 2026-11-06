# Phase 2 사전 고정 연구 규칙 — research-v1

이 문서는 구현 전에 고정한 실험 규칙이다. 승인된 LIVE 설정이 아니며 LIVE 위험 숫자는 계속 null이다.
초기 전략은 추세추종/돌파뿐이다. Phase 3 실시간 PAPER 엔진·AI·실거래는 구현하지 않는다.

## 시간과 데이터

- 입력은 단일 symbol의 연속·중복 없는 확정 1m OHLCV다. UTC interval 경계와 OHLC를 검사하고 상장 전 데이터는 거부한다.
- 5m/15m/1h는 완전한 1m 묶음만 집계한다. 결정 시각에 닫히고 사용 가능한 봉만 as-of join한다.
- historical_close 모드는 close+1ms/as_of 중 늦은 시각+명시적 latency를 정보 가용 시각으로 가정한다.
  이는 원래 거래소 데이터의 과거 수정 이력이나 실제 수신 지연을 복원하지 않는다. captured 모드는 received_at도 반영한다.
- EMA는 첫 N개 SMA seed 후 alpha=2/(N+1), ATR/ADX는 Wilder 방식이다. warm-up 이전 신호는 HOLD다.
- 피벗은 좌우 2개보다 엄격한 저점/고점이며 오른쪽 2개 봉이 닫힌 뒤에만 사용할 수 있다.

## 전략

기본 연구 가설: 1h EMA50/200, ADX14>=23, 15m EMA20/ATR14, 5m 실행.

추세추종:
- LONG은 EMA50>EMA200, SHORT는 반대다. 최근 4개 확정 15m 봉 중 EMA20과 low(LONG)/high(SHORT)의
  거리가 해당 ATR의 0.5배 이하인 되돌림이 있어야 한다. 현재 15m 종가는 EMA의 추세 방향에 있어야 한다.
- 5m 종가가 직전 3개 확정 5m 고점/저점을 돌파하면 후보를 만든다. 현재 봉은 breakout 기준에서 제외한다.
- 15m ATR/종가가 0.001..0.05 범위여야 한다. 손절은 entry±1.5ATR와 최근 확정 5m pivot 중 더 먼 쪽,
  목표는 2R, 최대 보유 4h다. 확정 pivot이 없으면 명시적 ATR fallback만 사용한다.

돌파:
- 1h 추세/ADX 조건은 동일하다. 현재 15m 종가가 직전 20개 15m 고점/저점을 돌파해야 하며,
  현재 volume>=이전 20개 평균*1.5다. 현재 봉은 가격/평균거래량 기준에서 제외한다.
- 5m 실행도 직전 3개 5m 고점/저점 돌파를 요구한다. ATR 범위/손절은 동일하며 목표 1.5R, 최대 보유 2h다.

두 전략 모두 결정 시 확정 5m 종가로 LIMIT 연구 후보를 만든다. tick은 명시적 방향 rounding,
quantity는 step 아래로 내린다. 동일 전략·symbol·결정봉·방향은 deterministic ID로 중복을 막는다.
후보는 위험 승인이나 실행 명령이 아니다. 실제 AI gate/위험 gate가 없으므로 실행기로 연결하지 않는다.

## 체결과 비용

- 신호를 만든 봉에는 진입하지 않는다. 결정/latency 이후 시작하는 1m 봉만 체결 후보로 사용한다.
- 두 체결 시나리오를 별도 보고한다: NEVER(체결 없음), TRADE_THROUGH(지정가를 tick buffer만큼 관통한 경우만 체결).
  touch만으로 체결을 보장하거나 체결확률을 추정하지 않는다. 시장가 추격·재호가는 없다.
- 진입 TTL은 연구용 5분이다. 신규 진입 가격은 지정가, 가격 개선은 가정하지 않는다. fee는 양방향 모두 부과한다.
- fill 봉에서 손절/목표가 같이 닿으면 손절 우선이다. fill 봉의 목표만 닿아도 선후 불명확하므로 이익을 확정하지 않는다.
- 이후 봉에서 SL/TP 동시 touch도 SL 우선이다. gap stop은 stop과 open 중 불리한 가격에 adverse slippage를 더한다.
- 펀딩은 시간·rate·mark-price 명세를 입력한다. entry/exit 봉의 순서가 불확실하면 불리한 지급은 반영하고
  유리한 수취는 확실히 보유한 경우만 반영한다. funding coverage가 부족하면 검사를 거부한다.
- fee/slippage/quantity/capital은 BACKTEST 전용 명시적 실험값이다. account tier·LIVE 위험값으로 복사하지 않는다.
- 단일 position/단일 pending, cash 이하 명목금액의 연구 모형이다. 실제 margin·liquidation·queue/partial-fill 모형은 없다.
- 데이터 마지막에는 강제 연구 정산하며 데이터 밖에서 보유를 이어 수익을 추측하지 않는다.

## 평가

고정 profile hash·dataset hash·데이터/펀딩 범위·가용성 가정·체결 시나리오를 리포트에 기록한다.
net PnL은 양방향 fee·adverse slippage·funding을 포함한다. 거래별 CSV·1m equity CSV·JSON 요약을 출력한다.
MDD는 1m close MTM 기준이며 실제 intrabar 최대 낙폭/수익성을 보장하지 않는다.
walk-forward는 순서대로 train prefix→최대 hold+TTL embargo→test를 적용하고 test 밖 신호/포지션을 넘기지 않는다.
최적화·확률적 체결률·미래 universe ranking은 사용하지 않는다. 합성 fixture 성과는 시장 성과가 아니다.

거래비용 제외 기준도 전략 구현 경계에 고정한다: 목표까지의 단위 gross 수익이
`entry*entry_fee_bps/10000 + target*exit_fee_bps/10000 + target*slippage_bps/10000`보다
엄격히 커야 후보를 만든다. 미래에 확정되는 funding rate/실현 수익을 이 선택에 사용하지 않는다.
펀딩의 실제 연구 비용은 체결 후 ledger에서 정산하므로 이 필터가 net 이익을 보장하지 않는다.

보유시간 만료와 데이터 말단 정산에서도 기존 목표보다 유리한 가격을 선택하지 않는다. 같은 봉의 진입·TP
순서를 모르는 상태에서 close/open이 목표를 넘어갔다고 초과 이익을 가정하지 않는다. 코드 구현 hash도
리포트에 저장하여 config/data가 같아도 코드 변경에 따른 결과를 구분한다.
