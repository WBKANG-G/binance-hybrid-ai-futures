# Binance 공개망 재검증 — 2026-10-19 (Asia/Seoul)

환경 설정 read 결과에 developers.binance.com, www.binance.com, fapi.binance.com, fstream.binance.com이
이미 포함돼 있었다. 같은 domain을 중복 추가하거나 불필요한 wildcard로 허용 범위를 넓히지 않았다.

| 실제 검사 | 결과 |
|---|---|
| 공식 REST Trade/변경 로그 HTTPS 본문 조회 | HTTP 200, 최신 문서 재확인 |
| `https://fapi.binance.com/fapi/v1/time` 공개 GET | proxy CONNECT 403 Forbidden |
| fstream 공개 경로 HTTPS 접속 진단 | proxy CONNECT 403 Forbidden |
| websockets 17.2/public_connect로 `wss://fstream.binance.com/public/stream?streams=btcusdt@depth@100ms` 실제 연결 | InvalidProxyStatus, response status 403 |

403은 Binance 응답을 받은 뒤의 451 지역 제한으로 관찰된 것이 아니라 프록시 CONNECT 단계에서 발생했다.
원격 프록시의 실시간 정책/설정 적용 상태를 이 도구로 직접 확인하거나 강제로 반영할 수 없다.
허용목록만 보고 접근이 해결됐다고 판단하지 않았다. 플랫폼 proxy 정책/런타임 적용 상태 확인 후 같은 정식 경로를
다시 검사해야 한다. 인증서 검증 해제·proxy 우회·대체 지역/호스트 사용·거래 키 요구는 하지 않았다.

서명/계정/주문/test-order endpoint 호출은 없었다. 실제 공개 시세·historical 데이터 수신은 아직 미검증이다.
Phase 2 연구 실행은 파일/합성 데이터로 독립 수행했으며 이를 실제 Binance 연결 결과로 표시하지 않는다.

공식 설계 근거는 [Phase 1 API 확인](phase1-binance-api-review.md)에 있으며 Phase 2 시작 때 Trade와
변경 로그를 다시 조회했다. Phase 2에서는 새로운 거래 API를 도입하지 않는다.
