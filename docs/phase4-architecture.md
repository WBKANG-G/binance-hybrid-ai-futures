> 후속 격리 모드 수정: 현재 Windows 절차는 `ISOLATED_START_HERE_KO.md`를 따릅니다.
> 아래 Cross 전용 설명과 이전 검증 결과는 당시 기록입니다. 격리 모드 실제 거래소 검증은 아직 미완료입니다.

# Phase 4 — DEMO 실행·복구 구현과 검증 경계

**구현/로컬 검증과 실제 DEMO 수락 검증을 구분한다.** 현재 REST/WSS는 프록시 403이고 DEMO 키가 없어
실제 인증·주문·체결·Algo 지원을 검증하지 못했다. 이 문서는 실제 거래소 동작 보장이 아니다.

## 격리와 기본값

- REST는 `https://demo-fapi.binance.com`, WSS는 `wss://demo-fstream.binance.com`으로 고정한다.
  임의 base URL, production 호스트, redirect, 구식 일반 조건부 주문, 신규 시장가를 거부한다.
- `configs/demo.example.yaml`은 DEMO 전용 sandbox이며 writes=false다. `--enable-demo`로 명시적 선택이 필요하다.
  일반 API는 계속 PAPER 기본값이고 주문/제어 POST가 없다. DEMO API 기동은 별도 CLI를 사용하도록 거부한다.
- 키는 direct runtime `BINANCE_DEMO_API_KEY`/`BINANCE_DEMO_API_SECRET`만 사용한다. generic/LIVE 키 변수와
  혼합되면 거부한다. 환경 변수 이름만으로 실제 키 발급 환경을 입증하지는 못하므로 DEMO 서버 인증도 필요하다.
  HMAC은 로컬 계산이므로 proxy placeholder 방식의 secret을 사용하지 않는다.
- API 키/secret/서명 URL/listenKey/raw 오류 body는 DB·브라우저·오류 로그에 저장하지 않는다.
  WSS logger를 차단하고 예외를 정제한다. TLS 검증은 유지한다.
- 기존 12개 OPEN LIVE 수치는 null, LIVE 시작/production submit은 계속 차단된다.

## 계좌·위험

SandboxPolicy의 순수 수학을 PAPER/DEMO에 공유하되 mode를 검증하고 저장소·venue·키를 분리한다.
DEMO는 실제 REST snapshot만 증거로 사용하고 PAPER exchange로 성공을 대체하지 않는다.
서명 전 시계 RTT/offset·freshness·recvWindow를 제한하고 rate limit 시 cooldown한다.

`/fapi/v3/account`의 wallet+unrealized equity, available balance, actual initial margin,
`/fapi/v3/positionRisk`, one-way/single-asset 및 `/fapi/v1/symbolConfig`를 확인한다.
계좌 모드/마진/레버리지는 자동 변경하지 않는다. 지원 범위는 USDT perpetual/cross/one-way이며
정책 이하의 실제 leverage를 요구한다. 모든 종목의 위험을 하나의 보수적인 상관 예산으로 합산한다.
실제 margin과 종목별 계획 margin, 아직 제출하지 않은 예약의 available balance도 검사한다.

계좌 equity에는 실현/미실현/수수료/펀딩이 이미 반영되므로 중복 차감하지 않는다. 원장 AccountValue의
realized bucket에는 분해 전 순 equity 변화를 넣는다. 이 값은 개별 실현손익 표시용이 아니다.
TRANSFER/WELCOME_BONUS를 ID로 중복 제거하여 입출금에서 분리한다. 실제 DEMO 지급/기타 income type,
fee asset 및 pagination 계약 검증은 남아 있다. KST 경계·손실 잠금은 기존 risk 함수를 적용한다.
명시적인 RULES_ONLY sandbox 외 AI 출력은 진입 권한이 아니다. 운영 AI는 Phase 5 범위다.

## 영속 주문과 single writer

`demo_accounts`, `demo_events`, `demo_outbox`를 `0003_demo` migration으로 추가한다.
PAPER/market 테이블과 기존 volume을 보존한다. DEMO- 계좌 namespace, 불변 정책, 계좌 행 잠금,
60초 lease/fencing, UTC·Decimal과 UUID 감사 이벤트를 사용한다.

의도/예약/SUBMITTING을 먼저 commit한다. 이후 별도 effect transaction이 계좌 행 잠금을 유지한 채
최대 socket timeout 5초의 HTTP를 호출한다. 새 leader는 진행 중인 writer와 동시에 요청할 수 없다.
lease를 넘긴 호출은 commit을 거부한다. HTTP 이후 DB commit 실패도 기존 SUBMITTING을 원본 ID로 조회한다.
DB lock timeout은 2초이며 충돌 시 fail closed다. heartbeat/정상 release와 새 leader 모두 검증한다.
network timeout은 전체 작업의 hard deadline이 아니며 실제 지연/장애 SLA는 미검증이다.

신호/client ID는 결정적이며 같은 신호를 중복 제출하지 않는다. HTTP 5xx/timeout/불명확 오류는 UNKNOWN이다.
조회 -2013은 제출 실패 증거가 아니므로 예약을 해제하거나 POST를 재시도하지 않는다. 명시적 거부만
미체결 예약을 해제한다. 취소 ACK/timeout만으로 해제하지 않고 실제 terminal 상태와 누적 체결을 조회한다.
REST 누적 체결량은 단조 증가를 검사하며 WS 중복/역순은 REST 재대조 hint로 처리한다.

## 보호·긴급 제어

일반 진입 `DE-`, 손절 `DS-`, 익절 `DT-`, reduce exit `DX-` ID를 분리하고 실제 server order/Algo ID도 저장한다.
SL/TP는 별도 `/fapi/v1/algoOrder`의 `CONDITIONAL` closePosition 주문이다. quantity/reduceOnly를 함께
보내지 않는다. MARK_PRICE / priceProtect=FALSE를 명시한다. 이것은 실제 트리거 방식의 DEMO 검증을 대체하지 않는다.

부분체결 후 SL을 먼저 생성하고 조회하여 client ID·활성 상태·반대 방향·가격·closePosition·one-way·
workingType·priceProtect를 확인한 뒤 PROTECTED로 기록한다. 그 후 TP를 생성·조회 검증한다.
TP 검증 실패는 기존 SL을 유지하고 진입을 잠근다. close-position SL은 해당 원웨이 포지션 전량을 대상으로 한다.
실제 서버의 추가 체결/취소/trigger 경합은 외부 검증 대기다.

SL 실패/조회 불능/위험 초과는 진입 차단·잔량 취소·긴급 reduce-only 시도로 연결한다.
Algo 조회 장애가 축소까지 막지 않도록 최소 positionRisk 조회로 실제 방향/수량을 다시 확인한다.
exit ACK는 완료가 아니다. 실제 exit FILLED와 position=0, 진입 잔량 정리를 확인해야 flat이다.
UNKNOWN exit는 재제출하지 않는다. 이전 exit가 FILLED로 확인됐는데 잔여 노출이 실제 대조되면 별도
ID의 축소 의도를 최대 3세대까지 만들 수 있다. 그 후에도 불명확하면 운영자 확인을 기다린다.

TP/SL FINISHED는 actualOrderId의 실제 체결도 조회한다. 부분 진입 중 TP가 체결되면 잔량을 먼저
취소하고 terminal 확인 이후 flat 처리한다. 실제 flat/미체결 진입 없음 확인 전 잔여 SL을 삭제하지 않는다.
외부 actor가 동시에 거래소 상태를 변경하는 것을 DB fence가 막을 수는 없으므로 전용 DEMO 계좌가 필요하다.

PAUSE_ENTRIES/SAFE_STOP/EMERGENCY_FLATTEN은 stable operation UUID로 처리한다. flatten은 flat 증거
이전 UNKNOWN이며 중복 operation으로 추가 exit를 보내지 않는다. NORMAL+fresh+REST 대조+private
연결이 확인된 경우만 명시적 CLI resume을 허용하고 손실/긴급/수동 변경 잠금을 풀지 않는다.
재인증 웹 제어와 하드락 해제는 구현하지 않았다.

## private stream과 프로세스

독립 CLI가 listenKey 생성/25분 갱신/종료와 `/private/ws/<key>` 연결을 수행한다. 모든 session mutation도
fence를 확인한다. handshake/pong 후 REST 대조 이전에는 진입하지 않는다. idle은 ping으로 확인하고,
끊김/만료/이상 이벤트는 진입을 차단한다. 정상 종료는 잔량 취소를 시도하고 SL/TP를 보존한다.
WS 중단 시 CLI를 다시 실행하여 새 연결·REST 대조를 수행한다. 자동 재연결 daemon은 제공하지 않는다.
정상 종료로 pause된 NORMAL 계좌의 재개는 명시적 --resume-entries가 필요하다.

API/웹 재시작과 DEMO worker는 별개다. worker 프로세스가 강제 종료된 동안의 신규 체결을 즉시 보호한다는
보장은 없다. durable leader 복구/재대조를 검증했지만 외부 process supervisor와 실제 장애 시간을 검증하지 않았다.
외부 알림 dispatcher, UI 인증, AI, 실제 수익/체결 확률 검증도 이 구현의 완료 주장에 포함하지 않는다.

## 2026-10-19 공식 문서 확인

- [General Info — DEMO REST/WSS](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info)
- [Trade — 일반/Algo 주문·조회·positionRisk V3](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade)
- [Account — V3·계좌 모드·symbolConfig](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account)
- [User Data Streams — private 경로/listenKey](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/user-data-streams)
- [Change log](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/change-log)

General Info의 demo-fapi/demo-fstream을 사용한다. catalog 헤더의 legacy testnet.binancefuture.com과
혼용하지 않는다. DEMO가 모든 endpoint를 지원한다고 가정하지 않으며 실제 지원 여부는 아직 미확인이다.
