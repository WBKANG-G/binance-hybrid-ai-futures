# 제출 의도 기록과 감사 조회 증거 연결 후보

원래 제출 기록·계정 신원 연결 단계의 후보 검증기를 추가했다. **선택한 로컬 의도 기록의 내용 일치는 검사하지만, 실제 제출 사실이나 원격 계정 신원 인증은 완료하지 않았다.** 현재 기록에 없는 정보를 만들어 통과시키지 않는다. Windows에는 설치하지 않았고 실제 거래소 요청·주문·과거 상태 변경은 없다.

## 조사한 기존 기록

`packages/persistence/demo.py`의 demo_events는 이벤트 UUID·계정 라벨·fence·UTC 시각·kind·전체 원장 payload를 저장하고 demo_outbox의 DEMO_AUDIT 행과 함께 커밋한다. 기존 테이블 자체는 이번 V2 응답 증거처럼 독립 증인과 연결된 append-only 전송 기록이 아니다. 같은 kind의 전체 원장 기록이 재호출로 생성될 수도 있으므로 선택한 INTENT 이벤트를 유일한 최초 제출로 인증할 수 없다.

`services/execution/demo.py`는 ENTRY_INTENT에서 SUBMITTING을 먼저 저장한다. STOP_LOSS의 SL_INTENT에는 algo_id와 SUBMITTING이 남는다. 그러나 REDUCE_ONLY_INTENT 이후 원격 positionRisk를 조회해 실제 청산 수량과 방향을 결정하며, 로컬 원래 진입 수량·open_quantity를 그대로 보낸다고 보장하지 않는다. INTENT는 전송 성공·실패·미전송을 증명하지 않는다.

`integrations/binance_usdm/demo_snapshot.py`의 fingerprint는 조회 데이터의 내용 해시다. 원격 계정의 고유 신원이나 자격 증명 연속성을 인증하는 값으로 사용하지 않는다. V2 읽기 메타데이터에는 원격 계정 ID 계약·신뢰된 계정 바인딩·요청 간 자격 증명 세션 연결 정보가 없다. 임의 JSON의 uid/accountAlias 필드를 읽어서 검증된 신원으로 승격하지 않는다.

## 구현한 검사

`services/execution/demo_submission_link_candidate.py`에 다음 읽기 전용 함수를 추가했다.

- `inspect_intent_reference`: 알려진 이벤트 UUID와 계정으로 한 행만 선택하고, 내용을 검토하기 위한 envelope SHA-256을 반환한다. 전체 이력을 검색하지 않는다. 이 해시는 호출자가 보관하는 내용 핀이며, 생성 시점의 과거 진실·외부 공증을 뜻하지 않는다.
- `review_submission_link`: 그 핀과 현재 선택한 이벤트 내용이 같은지 검사하고, 기대 주문 정보를 원장에서 도출해 V2 조회 관측과 대조한다. 호출자가 별도의 수량·방향을 넣어 대체하는 인자를 제공하지 않는다.
- `review_identity_capability`: 정확한 바이트가 감사 체인에 결합된 계정 조회인지 검사하고 현재 프로토콜의 신원 인증 부재를 보고한다. 입력 JSON 안의 주장이나 로컬 fingerprint를 신뢰 앵커로 쓰지 않는다.

의도 이벤트는 REPEATABLE READ/READ ONLY 트랜잭션으로 읽는다. UUID·계정 PK 조건으로 payload 크기부터 확인하고, 1 MiB 이하만 가져온다. SQL statement_timeout 2초, lock_timeout 200ms를 설정한다. orders 128개·requests 768개 한도를 적용하며, 앞서 문제가 된 수천 개 전체 이벤트 본문 전송은 하지 않는다. 크기 검사 후 다른 트랜잭션이 내용을 바꾸면 현재 읽기는 같은 스냅샷을 사용하고 다음 검토는 핀 불일치를 감지한다. 이 스냅샷과 별도 V2 조회 검토 전체를 원자적 단일 스냅샷이라고 주장하지 않는다.

kind/account/fence/DEMO_AUDIT outbox·DEMO/BTCUSDT signal·SUBMITTING 상태를 검사한다. 요청 ID가 한 부모 주문의 올바른 역할 필드에 한 번만 나타나야 한다. ENTRY/STOP_LOSS/EXIT를 서로 대신 사용하지 않는다. 원래 client ID 조회만 연결하며 숫자 orderId 조회의 역사적 대응 관계를 추정하지 않는다.

ENTRY는 의도 원장의 방향·수량·LIMIT/GTC·지정가를 조회 결과와 비교한다. STOP_LOSS는 반대 방향·STOP_MARKET·트리거 가격·CONDITIONAL·MARK_PRICE·priceProtect=false를 비교하고, 알고리즘 상태를 실행 하위 주문 체결 증거로 사용하지 않는다. 이 범위를 벗어나는 가격 보호 응답 표현이나 주문 스타일은 거부한다. HTTP 오류에는 정상 주문 조건 일치를 부여하지 않으며 NOT_FOUND는 미해결로 유지한다.

EXIT는 의도와 원래 ID 조회의 내용 연결만 검사하고 EXIT_WIRE_QUANTITY_NOT_PERSISTED 차단을 반환한다. 응답 origQty나 원래 진입 수량을 실제 전송 수량의 대체 증거로 쓰지 않는다. ENTRY/SL의 조건 일치도 실제 전송을 인증하지는 않는다.

## 결과와 차단

`local_intent_content_matches`는 선택한 핀과 로컬 이벤트 내용이 일치한다는 뜻이다. `intent_order_terms_match`는 지원하는 ENTRY/SL의 정상 응답 조건까지 일치할 때만 true다. 두 값은 아래 권한을 부여하지 않는다.

- local_submission_binding_verified / wire_submission_occurred_verified / intent_is_unique_original_submission_proven: false
- remote_account_identity_verified / credential_session_binding_verified: false
- submission_precedence_verified / cross_source_atomic_snapshot_verified / fresh_exchange_state_verified: false
- remote_request_resolution_verified / no_send_verified / position_closed_verified / archive_authorized: false

미래 조회가 아직 없다는 사실이나 서명 timestamp만으로 시간 선후를 인증하지 않는다. 조회 응답과 로컬 이벤트가 같은 계정·세션에서 생성됐다는 증명도 보류한다. 원문·키·원래 주문 ID·임의 계정 식별 필드는 보고서에 출력하지 않는다. 보고서에는 내용 참조 해시와 판정만 남긴다.

DB·원장·기존 요청 상태·파일을 바꾸지 않으며, 모든 실행·주문·LIVE 승인은 false다. Windows의 기존 네 미해결 요청과 34개 주문 이력, 용량 부족은 해결되지 않았다. 과거 의도·본문 해시를 V2 전송 증거로 소급 인증하지 않는다.

후속 작업은 실제 전송 직전의 불변 요청 내용과 안전한 자격 증명 세션 바인딩을 남기는 별도 감사 계약이다. 그 계약도 로컬 세션과 원격 고유 계정 신원을 구분해야 하며, 지원되는 신원 출처·신뢰 앵커가 확보되기 전까지 원격 신원 차단을 유지해야 한다. 설치·상태 조정·거래 재개는 포함하지 않는다.

정확한 테스트 결과는 `SUBMISSION_LINK_CANDIDATE_VALIDATION.json`에 기록한다. 테스트는 로컬 PostgreSQL/SQLite/파일과 합성 응답만 사용한다.
