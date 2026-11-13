# 감사 응답을 이용한 요청별 결과 판정 후보

V2 응답 증거를 입력으로 쓰는 후보 검증기와 비공개 원문 보존 함수를 구현했다. Windows 설치·실제 거래소 조회·기존 미해결 요청 조정은 수행하지 않았다. 자동 실행 진입점이나 설치 명령은 추가하지 않았다.

## 입력과 신뢰 범위

`services/execution/demo_request_outcome_candidate.py`의 `review_request_outcome`은 증거 저장소·저널 scope/UUID·로컬 계정 라벨·감사 sequence·정확한 응답 바이트·기대 주문 정보를 받는다. PG 감사 체인, 체크포인트, V2 증거 체인과 독립 파일 증인을 검증한 뒤 OBSERVED FINISH 한 건을 선택한다. 본문 길이와 SHA-256이 그 행에 결합된 값과 일치해야 내용을 판정한다. 예약 행, 누락된 sequence, 다른 계정 라벨, 과거 별도 출력 해시를 대신 사용할 수 없다.

기대 주문 정보는 호출자가 제공하는 role/client ID/방향/종류/수량/선택적 거래소 ID다. 이 정보는 **원래 제출 요청의 신뢰할 수 있는 저장 기록과 아직 연결되지 않았다**. 로컬 DEMO 라벨도 원격 거래소 계정 신원 증명이 아니다. 따라서 결과는 감사된 GET의 조회 당시 관측이며, 원격 요청 결과 확정·청산 완료·미전송 증명·이력 보관 인증서가 아니다. 오래된 관측도 새 조회로 표현하지 않는다.

## 판정 규칙

일반 ENTRY/EXIT는 `/fapi/v1/order`만 받는다. 원래 client ID로 조회했는지, 숫자 orderId 조회라면 기대 거래소 ID를 명시했는지 확인한다. 응답의 symbol/clientOrderId/orderId/side/type/positionSide/reduceOnly와 기대 수량을 대조한다. BTCUSDT·BOTH 범위이며 ENTRY는 LIMIT, EXIT는 LIMIT/MARKET만 지원한다.

수량은 제한된 십진 문자열을 Decimal로 비교한다. 체결량은 0 이상 원래 수량 이하여야 한다. FILLED는 전량 체결, NEW/REJECTED는 체결량 0, PARTIALLY_FILLED는 0 초과 전량 미만이어야 한다. 지원하는 종료 상태는 FILLED/CANCELED/REJECTED/EXPIRED/EXPIRED_IN_MATCH다. 일부 체결 후 취소·만료는 체결·노출 후속 검증이 필요하다고 표시한다. 전량 체결도 포지션 종료를 뜻하지 않는다.

STOP_LOSS는 `/fapi/v1/algoOrder`의 clientAlgoId 조회를 별도로 검증한다. clientAlgoId/algoId/side/orderType/closePosition/positionSide/symbol을 확인한다. STOP_MARKET·closePosition=true 범위에서 NEW/CANCELED/EXPIRED/REJECTED/TRIGGERED/FINISHED 관측만 지원한다. 알고리즘 상태를 일반 주문의 체결·청산 증거로 승격하지 않으며 underlying execution proof가 계속 필요하다. 지원하지 않는 필드 형태나 상태는 거부한다.

HTTP 200이 아닌 응답은 정상 주문 상태로 해석하지 않는다. 코드 -2011/-2013은 NOT_FOUND_UNRESOLVED다. 다른 오류도 HTTP_ERROR_UNRESOLVED로 남긴다. 오류 본문·메시지·주문 원래 식별자는 보고서에 출력하지 않는다. JSON 객체만 받으며 중복 키·비유한 상수·잘못된 UTF-8·불완전 JSON은 거부한다.

## 비공개 원문 아티팩트

`preserve_response_artifact(..., apply=True)`는 명시적으로 호출했을 때만 검증된 원문을 보존한다. 검토만 하는 기본 경로는 아무 파일도 만들지 않는다. 원문은 4 MiB 이하이고, 런타임 디렉터리 밖의 사전 준비된 소유자 전용 0700 디렉터리에 0600 파일로 저장한다. 이름은 scope/저널/라벨/증거 행 해시/정책으로 계산한 digest이며 원래 주문 ID를 파일명에 넣지 않는다. 원문에는 비공개 정보가 있을 수 있으므로 저장소·전송 패키지·브라우저 결과에 포함하지 않는다.

새 파일은 O_EXCL/O_NOFOLLOW로 만들고 파일·디렉터리를 fsync한다. 같은 내용의 명시적 재호출은 파일을 다시 쓰지 않는다. 기존 파일이 다르거나 심볼릭 링크·부적절한 권한이면 거부한다. 일부 쓰기·fsync 실패는 성공 결과를 반환하지 않는다. 부분 파일은 후속 호출이 자동 완성하거나 삭제하지 않는다.

`review_response_artifact`는 비공개 파일을 읽고 전체 감사 체인·본문 해시·아티팩트 참조 바인딩을 다시 검증한다. 파일 자체는 별도 보안 저장소나 외부 공증이 아니며, 아티팩트가 존재해도 권한이 생기지 않는다. 총 저장량·보존 기간 정책, 원격 계정 바인딩, 원래 제출 기록과의 연결, 알고리즘 실행 하위 주문 검증, 인증서 서명·채택·마이그레이션은 구현 범위 밖이다.

## 운영 상태와 후속 경계

보고서의 remote_request_resolution_verified/local_submission_binding_verified/remote_account_identity_verified/position_closed_verified/no_send_verified/archive_authorized는 모두 false다. DB·감사·과거 요청 상태는 바꾸지 않는다. 원문 가져오기만 별도 비공개 디렉터리를 변경한다. 모든 경로에서 주문·실거래·실행 승인은 false다.

기존 Windows의 UNKNOWN 3건·SUBMITTING 1건과 34개 이력 주문은 이 구현으로 해결되거나 삭제되지 않는다. V2 이전 두 FILLED 관측과 두 NOT_FOUND 관측을 소급 결합하지 않는다. 현재 용량 부족과 주문 사용량·공인 IP 용량 차단도 유지된다.

다음 검토 대상은 기대 주문 정보를 원래 제출 기록의 불변 참조에 연결하고, 그 기록과 계정 신원 증거를 함께 검증하는 경로다. 이 후보를 Windows에 배포하거나 요청 상태를 확정하는 승인은 포함하지 않는다.

테스트는 실제 로컬 PG/SQLite/파일과 합성 HTTP 응답만 사용했다. 결과는 `REQUEST_OUTCOME_CANDIDATE_VALIDATION.json`에 기록한다.
