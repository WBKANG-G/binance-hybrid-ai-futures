# Phase 1 Binance 공식 API 재확인

확인일: 2026-10-19 (Asia/Seoul). 공식 HTTPS 문서 본문을 조회했다. 거래 주문 API에는 요청하지 않았다.
이전 endpoint별 legacy 링크 일부는 HTTP 200으로 홈페이지를 반환해, 상태 코드만으로 확인하지 않고
현재 product/catalog의 실제 본문과 schema를 사용했다.

| 공식 자료 | 확인한 계약 |
|---|---|
| [REST Trade](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade) | 일반 `POST /fapi/v1/order`; 조건부 `POST /fapi/v1/algoOrder`, algoType=CONDITIONAL, clientAlgoId/algoId 분리 |
| [REST Market Data](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data) | time, exchangeInfo, klines, depth, bookTicker, premiumIndex, fundingRate, openInterest의 GET 경로 및 응답 |
| [General Info](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info) | query HMAC-SHA256, API key header, timestamp/recvWindow, 불명확한 주문 결과 재대조 필요 |
| [WebSocket Connect](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Connect) | public/market/private 라우팅, ws/stream 연결 형식, 연결 24h·ping/pong |
| [Public Streams](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/public) | depth, bookTicker는 /public |
| [Market Streams](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/market) | kline, aggTrade, markPrice는 /market |
| [Local Order Book](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/How-to-manage-a-local-order-book-correctly) | 연결 후 이벤트 buffer → REST snapshot → u<lastUpdateId 제거 → 첫 U<=lastUpdateId<=u → 이후 pu==이전 u. 수량은 절대값, 0은 삭제 |
| [User Data Streams](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/user-data-streams) | /private/ws/listenKey, 유효기간/연장, ORDER_TRADE_UPDATE·ALGO_UPDATE 별도 이벤트, listenKeyExpired |
| [User Event Schemas](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/~schemas#algoupdate) | 일반 i/c/X/z와 Algo aid/caid/X를 실제 schema에서 구분 |
| [Change Log](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/change-log) | 2025-12-09 Algo 이관, 최신 2026-10-15 공지/10-16 적용 항목 재확인 |

신규 진입 계약은 LIMIT만 만들며 price·quantity는 tickSize/stepSize로 검사한다.
pricePrecision/quantityPrecision은 필터 단위로 사용하지 않는다. MIN_NOTIONAL과 PERCENT_PRICE도 검사한다.
자동 rounding이나 시장가 추격은 없다. 별도 floor helper는 명시적으로 호출할 때만 사용한다.
close-all 보호 계약은 closePosition=true, quantity/reduceOnly 미포함, workingType은 호출자가 명시해야 한다.
이는 [OPEN] 계정 모드·트리거 정책의 확정이나 주문 허가가 아니다.

서명은 오프라인 SecretStr 입력과 테스트 벡터로만 검증한다. proxy placeholder가 HMAC용 raw secret이라는
가정을 하지 않는다. 실제 Binance 키를 요구·읽기·저장하지 않았다. signed account request, listenKey
생성/갱신과 private 연결은 후속 승인 단계까지 미노출이다. 현재 public connector는 private URL을 거부한다.

실제 공개 REST time 및 public WS 접근은 cloud proxy CONNECT 403으로 차단됐다. fapi.binance.com과
fstream.binance.com 허용 요구를 draft에 저장했으며, 적용 후 실제 서버의 시세·재연결을 다시 검사해야 한다.
이 제한을 피하려고 다른 호스트나 지역으로 우회하지 않았다. 로컬 fixture 결과를 실거래/DEMO 검증으로 표시하지 않는다.

private.py는 listenKey의 60분 lease, 명시적 갱신 확인, listenKeyExpired 무효화, 이벤트 종류별 E 시각 순서와
중복 억제를 오프라인으로 검증한다. 이 lease도 계정 재대조 완료를 주장하지 않으며 실제 키 발급/keepalive/접속은 수행하지 않는다.
