# Binance 공식 API 확인 — Phase 0

확인일: 2026-10-19 (Asia/Seoul). 공식 HTTPS 문서를 GET으로 조회해 HTTP 200과 아래 본문을 확인했다.
최초 프록시 403은 문서 도메인 허용 설정 후 해소됐다. 거래 REST endpoint, test-order endpoint와 WebSocket에는
어떤 요청도 보내지 않았다. 아래는 설계 근거이며 실행 가능한 주문 예제가 아니다.

| 확인 항목 | 공식 문서 | 확인 결과 |
|---|---|---|
| 일반 주문 | [REST Trade / New Order](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade#new-order) | `POST /fapi/v1/order`, signed endpoint. 프로젝트 신규 진입은 LIMIT만 허용 |
| 조건부 보호 주문 | [REST Trade / New Algo Order](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade#new-algo-order) | `POST /fapi/v1/algoOrder`, `algoType=CONDITIONAL`, STOP_MARKET/TAKE_PROFIT_MARKET 지원, 별도 algoId/clientAlgoId |
| 보호 조회 | 같은 Trade 문서의 Query Algo Order / Current All Algo Open Orders | `GET /fapi/v1/algoOrder`, `GET /fapi/v1/openAlgoOrders` |
| WebSocket 라우팅 | [Connect](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Connect) | `/public`: 고빈도 공개 시세, `/market`: 일반 시세, `/private`: 사용자 데이터 |
| 변경 로그 | [USDⓈ-M Change Log](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/change-log) | 2025-12-09 조건부 주문 Algo Service 이관, ALGO_UPDATE 이벤트 참조. 최신 2026-10-15 공지와 10-16 적용 항목 확인 |

WebSocket 문서는 2026-10-16 수정으로 표시되며 `/public/ws/...@depth`,
`/market/ws/...@aggTrade`, `/market/stream?streams=...@markPrice` 예시를 제공한다.
라우팅 경로 없는 연결은 public 데이터만 받으며 market/private 스트림은 push되지 않는다고 명시한다.
연결 수명 24h, ping/pong과 재연결 정책도 후속 어댑터에서 검증한다.

closePosition=true는 STOP_MARKET/TAKE_PROFIT_MARKET close-all용이고 quantity 및 reduceOnly와 동시 전송할 수 없다.
priceProtect는 MARK_PRICE/CONTRACT_PRICE 차이 조건으로 보호 트리거를 지연시킬 수 있다.
일반 주문 catalog에 과거 조건부 type 열거가 보이더라도 명세와 Algo 전용 문서/이관 공지를 기준으로
조건부 경로를 분리한다. Phase 1에서 해당 규칙의 실제 wire 계약 테스트를 작성해야 한다.

2026-10 변경 로그는 leverage의 고정 최대 125 표기를 제거하고 symbol별 bracket 조회를 요구한다.
이를 프로젝트의 사용자 위험 상한으로 사용하지 않는다. 위험 상한은 계속 [OPEN]이다.
다음 단계 시작 시 공식 문서와 변경 로그를 다시 확인해야 한다.
