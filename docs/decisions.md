# 현재 결정 — Phase 2

[LOCKED]·미확정 LIVE 위험 숫자는 유지한다. 기존 공개 데이터 어댑터/영속성 위에 독립적인 오프라인 연구 모듈을 추가했다.

- services/strategies는 frozen 연구 후보만 만들고 exchange/persistence transport를 호출하지 않는다.
  backtest는 실제 주문·실시간 PAPER 엔진과 분리한다. AI/위험 승인 gate가 없으므로 실행기로 연결하지 않는다.
- 1m 데이터를 검증한 뒤 완전한 5m/15m/1h만 만들고, close/as_of/선택적 received_at에 따른 가용 시각으로 join한다.
  지표는 prefix-stable이며 확인된 pivot만 사용한다. 미래 데이터를 바꿔도 실제 LONG/SHORT 후보가 동일함을 검증한다.
- 연구 수식/실험 숫자는 phase2-research-protocol.md와 별도 BACKTEST profile에 고정한다. LIVE profile과 호환되지 않는다.
- 가격·수량·비용·펀딩·equity는 Decimal이다. 지표/시뮬레이션의 연산 context는 명시적으로 고정한다.
- 후보 선택에 미래 funding/수익을 쓰지 않는다. 알려진 고정 fee/slip 이후 목표 이익이 양수인 경우만 후보를 만든다.
- OHLC로 알 수 없는 순서를 유리하게 가정하지 않는다. SL 우선, fill 봉 TP 미확정, gap stop 불리한 open,
  TTL 초과/시장가 추격 금지, EOD/timeout에서 목표 초과 이익을 선택하지 않는다.
- NEVER/TRADE_THROUGH는 체결 가정별 실행이며 확률·실제 PnL 범위가 아니다. queue/partial-fill/청산/margin은 미모형화다.
- 비용·펀딩 ledger를 포함한 현금 흐름과 1m close MTM을 기록한다. 음수 equity도 숨기지 않고 추가 진입은 cash 노출 제한을 받는다.
- forward window는 과거 warm-up과 최대 hold+TTL embargo를 가진다. 파라미터 검색이나 test 결과 재학습은 없다.
- hash·가정·원본 입력·후보·거래·equity를 export하며 nonempty output을 덮어쓰지 않는다.
- 현재 검증 데이터는 SYNTHETIC이다. 고정 종목·정적 tick/step/상장 metadata는 명시된 연구 가정이며
  과거 거래소 revision/필터 변경·전체 universe의 survivorship 편향을 해결했다고 주장하지 않는다.

public REST/WSS는 허용목록 존재 상태에서도 proxy 403이다. 실제 Binance 데이터 연결 검증은 별도 외부 blocker로 남는다.
Phase 3 착수에는 사용자 승인이 필요하다.


## Phase 3 승인 후 추가 결정 (2026-10-19 UTC)

위의 Phase 3 미착수 기록은 Phase 2 당시 상태다. 사용자 승인으로 Phase 3을 구현했고 Phase 4는 미착수다.
위험·영속성·복구를 우선하여 PostgreSQL 계좌 행 전체 잠금과 lease/fencing을 사용한다. 모든 종목을
하나의 상관 예산으로 보수 처리한다. PAPER 정책은 별도 명시적 sandbox이며 기존 OPEN LIVE 값은 null이다.
API는 worker를 시작하지 않는다. 실제 SDK transport/외부 알림/인증 제어는 추가하지 않았다.
상세 계약과 실제 테스트 결과는 phase3-architecture.md 및 phase3-acceptance.md에 기록한다.


## Phase 4 승인 후 결정 (2026-10-19 UTC)

Phase 4는 사용자 승인으로 구현했고 실제 DEMO 수락은 network 403/키 부재로 미완료다.
DEMO host·credentials·계좌 원장을 분리하며 production submit은 계속 차단한다. 기본 preflight는
읽기 전용이고 unknown POST/Algo/exit는 반복하지 않는다. 자세한 구현/검증 범위는 phase4 문서에 있다.
이전 Phase 3 보고서의 다음 단계 승인 대기 문구는 당시 기록이며, Phase 5는 아직 승인되지 않았다.
