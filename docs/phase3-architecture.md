# Phase 3 — 오프라인 PAPER 위험·실행 계약

이 단계는 **별도 명시적 샌드박스**다. API 기동은 PAPER worker를 시작하지 않으며, 주문·제어 POST는
계속 미노출이다. LIVE/DEMO 시작과 Binance 실제 submit은 차단된다. `paper.example.yaml`과
`live.example.yaml`의 OPEN 위험 숫자 12개는 null 그대로다. 신규 `paper-sandbox.example.yaml`의
수치는 합성 시뮬레이션 가정이며 실거래 승인값이 아니다. 연구 후보는 자동 실행되지 않는다.

## 위험 게이트와 수량

PAPER Signal·정책 version·확정봉/TTL·시장/계좌/상관 snapshot·시계·재대조·regime·실행비용을 확인한다.
AI 단계 이전이므로 명시적 RULES_ONLY 샌드박스만 허용하고 HOLD/불명확 입력은 진입을 거부한다.
AI override 필드는 정책에 없고, 원장 트랜잭션은 정책 snapshot 변경도 거부한다.

`B = equity * risk_per_trade_pct / 100`.
단위손실은 진입/손절 차이 + 불리한 stop slippage + 양방향 수수료 + funding allowance이다.
수량은 LOT_SIZE step으로 내림하고 tick/step/min notional/percent price를 재검사한다.
동시 포지션·단일/전체 notional·leverage에 따른 margin·상관 위험 cap을 모두 통과해야 한다.
실제 체결 후 평균가/수수료/equity/손절위험/notional/margin을 재계산하고 초과 시 EMERGENCY 축소한다.

초기 보수 정책으로 **모든 종목을 하나의 상관 위험 예산**으로 취급한다. 입력 group 이름을 바꿔
한도를 회피할 수 없다. 실제 rolling correlation 산출·동적 유니버스는 Phase 6 범위다.
미체결 예약 + 실제 포지션 위험을 합산한다. 보호 미확인 수량은 손절거리 대신 전액 notional을
보수적인 위험으로 부과하며, 보호 검증 이전에는 새 진입이 차단된다.

평가액은 initial + realized + unrealized - fees + funding + net_deposits이다. 손실 gate는
입출금을 제외한 adjusted equity로 계산한다. KST 00:00 및 설정의 고정 week_start로 기간을 나눈다.
기간 시작 평가가 누락되면 임의 baseline을 만들지 않고 UNHEALTHY로 차단한다. 손실/긴급 잠금은
시간 경과·stale·재시작으로 풀리지 않는다. 재인증을 갖춘 운영 재개 UI는 후속 단계다.

## 영속성과 실행 순서

PostgreSQL `paper_accounts`의 PK 계좌 행을 `SELECT FOR UPDATE`하여 계좌 전체를 직렬화한다.
원장의 signal UUID key는 계좌 내 유일하며, 다른 payload로 재사용하면 거부한다. client order ID는
계좌+signal hash로 결정한다. 의도·예약·SUBMITTING은 venue 호출 전에 별도 commit된다.
`paper_venues`는 별도 영속 시뮬레이터 상태다. 체결 ID를 중복 계산하지 않고 충돌은 거부한다.

계좌 leader lease+증가 fencing token은 원장과 venue 양쪽의 모든 쓰기를 확인한다. 새 리더는
재대조 이전 진입 불가다. DB 장애/lease 만료/중복 리더는 side effect 이전 차단한다.
정책과 금액은 versioned JSON snapshot/Decimal 문자열로 저장된다. 계좌 단위의 거친 잠금과 전체
snapshot audit는 안전성 우선의 Phase 3 구현이며 대규모 처리량 검증은 하지 않았다.

각 상태 변경과 UUID 감사 이벤트, outbox 행은 같은 DB 트랜잭션이다. outbox는 영속되지만 외부
알림 dispatcher는 이 단계에 없다. 기존 market 테이블은 보존하며 migration `0002_paper`를 추가한다.

응답 유실/프로세스 종료로 남은 SUBMITTING/UNKNOWN은 같은 client ID로 조회한다. 자동 재제출하지
않는다. simulator에서 확정된 absence만 REJECTED로 정리할 수 있다. 취소 요청/timeout은 잔량 취소
증거가 아니며, 후속 체결을 계속 대조한다. terminal 조회 후 미체결분 예약만 해제한다.
원격 revision이 달라지면 신규 예약은 거부하고, 재대조 완료 시 원격 수량/체결/상태/보호를 다시 비교한다.

## 보호와 종료

부분체결 즉시 STOP_MARKET을 만들고 활성 상태·반대 방향·trigger·coverage·MARK_PRICE를 조회하여
검증한 뒤 PROTECTED로 기록한다. Algo ID는 `PA-`, 일반 진입은 `PE-`, reduce exit는 `PX-`로 분리한다.
시뮬레이터에서는 원웨이 close-position 보호를 원자 갱신한다. 실제 Binance의 보호 교체·수량 갱신
원자성, 체결·취소 경합은 검증되지 않았으며 Phase 4에서 별도로 확인해야 한다.

STOP 실패/잘못된 보호/실제 위험 초과는 신규 진입을 차단하고 잔량 취소와 reduce-only exit를 시도한다.
exit 의도/ID도 먼저 commit하며 응답 ACK만으로 완료라 하지 않는다. 후속 실제 수량 0 확인이 필요하다.
재시작 시 exit의 존재/결과가 불명확하면 UNKNOWN을 유지하고 무조건 재주문하지 않는다.

PAUSE_ENTRIES는 미체결 진입을 취소하고 보호를 유지한다. SAFE_STOP은 진입만 막고 보호 관리를
계속한다. EMERGENCY_FLATTEN은 실제 flat+잔량 정리+재대조 확인 전 UNKNOWN이다. operation_id
재실행은 기존 작업을 이어가며 같은 exit가 중복 제출되지 않는다. HTTP 제어/인증은 구현하지 않았다.

명시적인 순서가 있는 MARK_PRICE tick만 SL/TP를 실행한다. SL 우선, 갭 손절은 불리한 mark,
TP는 target보다 유리한 가격을 부여하지 않는다. TP는 로컬 PAPER 조건이며 실제 Algo TP가 아니다.
잔여 SL은 실제 flat 확인 뒤 정리한다. 신규 진입은 LIMIT/GTC만 사용하며 시장가 추격은 없다.
실제 호가 queue/지연/체결 확률/강제청산은 재현하지 않는다. 체결은 명시적 합성 유동성 이벤트이며,
OHLC touch를 체결로 추론하지 않는다. 계획 위험은 갭/슬리피지로 초과할 수 있다.

## 공식 문서 확인 (2026-10-19 UTC)

- [USDⓈ-M Trade](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade)
- [변경 이력](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/change-log)

HTTP GET으로 실제 문서 내용의 Algo 경로/식별자/closePosition 제약을 확인했다. 최신 이력에
2026-10-15 공지 및 10-16 시행 항목이 존재한다. 보호는 `/fapi/v1/algoOrder`의 별도 계약이며
closePosition=true에 quantity/reduceOnly를 함께 보내지 않는다. 기존 오프라인 주문 계약 테스트를
유지했다. 실제 주문/test-order 요청은 하지 않았다. 공개망 proxy 403은 이전 단계의 미해결 제한이다.
