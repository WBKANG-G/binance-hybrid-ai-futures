# 수신/REST 분리와 유효 알림 통합 검증

## 실제 관측

사용자 작업a823ff80-5903-4990-8ac2-ae3707b5ec84: FULL_FILL0.0007, stop_verified/target_verified=true, FINISHED_FLAT/CLOSED이지만 session_error=true/전체수락false. 진단은 stage EVENT, code EVENT_TIME_INVALID, event_kind ORDER_TRADE_UPDATE, event_age_ms30105, ready_callback_ms21065입니다. completion/database_failure는 NONE, 마지막 스냅샷 불일치 필드는[]입니다. 처리 시30초를 넘긴 것이 직접 차단 원인입니다. 구버전에 수신 시각이 없어 네트워크/거래소 지연과 내부 대기 시간을 분리해 실제 원인을 완전히 확정한 것은 아닙니다.

## 부모 버전 재현

부모 ZIP 전체 소스를 그대로 추출하고 새 회귀 검사 두 개만 추가했습니다. 콜백/REST 대기 중 새 이벤트가 전달되는 합성31초 흐름에서 두 경우 모두 이전판은 진입·SL/TP·청산을 확인한 뒤 EVENT_TIME_INVALID/event_age_ms31000/session_error=true/전체수락false로 실패했습니다. 2 failed, 1 deselected in1.78초. 실제 사용자 수신 지연을 측정한 것은 아니며 내부 대기 문제를 구체적으로 재현한 검사입니다.

## 수정 범위

EventInbox를 추가하고 demo_stream의 수신과 직렬 DB/REST 작업을 분리했습니다. 수신 작업은 원래1MiB 한도/JSON 검사/알려진 이벤트 종류/30초 이내·미래 아님을 모든 메시지에 적용합니다. listenKeyExpired와 대기128개 초과는 실패입니다. e/E만 임시 보유하며 정상 알림들을 최신 원래 시각의 재대조 요청으로 통합합니다. 대표 알림은 바꾸지 않은 DemoCoordinator.event의 처리 시점30초 검사와 원래 REST 대조/보호 검사를 다시 통과해야 합니다. 체결 증감 연산은 하지 않습니다.

수신 실패 즉시 메모리 session_failed로 예약·재개와 HTTP 진입 제출 직전의 신규 주문을 차단합니다. 제출 직전 차단은 LOCAL_VALIDATION_FAILED 무전송 증거와 기존 UNKNOWN/예약을 보존하며 자동 재전송하지 않습니다. 이미 전송 중인 HTTP를 취소할 수 있다고 주장하지 않습니다. DB 정리/감사는 진행 중인 settled_call 작업을 완료한 뒤 실행합니다. 수신 실패가 검증 제한시간 취소 중 발생해도 실패 표시/진단을 보존합니다. 수신 작업을 먼저 중지한 뒤 의도적으로 소켓을 닫아 거짓 실패를 만들지 않으며 수신 종료 대기 중의 외부 취소도 전파합니다.

기존 SDK/demo_snapshot/coherent_snapshot/account_risk/저장·fencing/정책/주문 파라미터·수량·보호·청산/기간 기준/키 보관/Docker·Compose/export는 바이트 단위로 그대로입니다. demo.py에서는 수신 실패 시 예약/재개/제출 차단만 추가합니다. 이벤트 허용 목록과30초 검사를 늘리거나 raw E를 현재 시각으로 바꾸지 않았습니다. 정상 알림의 개별 event() 재생 대신 REST 재대조를 통합하는 구조 변경이며, 모든 프레임을 수신 시 검사합니다.

원래 DEMO의 WS 원본 장기 보관 한계는 이 변경의 별도 수락 과제로 남아 있습니다. REST와 감사 원장 대조를 유지하지만 이번 변경으로 전체 WS 원본 보관 요구를 완료했다고 주장하지 않습니다. 키/URL/원본 오류 문자열을 로그/DB에 새로 저장하지 않습니다.

## 실제 검사

신규16개 검사 통과(5.56초). 최신/역순 정상 알림 통합과 e/E 외 원본 미보유, 정상 알림 사이의 잘못된/미래/오래된 프레임과 JSON/객체 오류·연결 키 만료 차단, 대기 한도 초과, 조용한 수신 제한시간과 정상 중지, 콜백/REST 지연 두 상황의 실제 PG·로컬 WS·SL/TP·단일 reduce-only 종료 후 전체 검증 통과를 확인합니다. 수신 실패의 예약/재개/실제 제출 직전 차단과 기존 의도 보존/무전송 증거·미재전송, DB 행 잠금 중 수신 오류가 발생해도55P03 없이 작업을 끝낸 뒤 감사/정리하는지, 수신 중지 대기 중 취소가 전파되는지도 확인합니다.

기존 관련 검사와 합친58개 집중 실행도 통과(29.81초). 기존의 단일31초 오래된 이벤트 거부, 세션 오류가 정리 중 취소에 가려지지 않는 검사, worker drain/행 잠금 회귀와 최초 체결 보호 검사 등을 포함합니다. 신규 최종16개에는 이후 추가한 제출 직전/수신 종료 취소 검사도 포함합니다.

- 전체 pytest: 865 passed in 207.13s (0:03:27). TEST_DATABASE_URL은 실제 로컬 PostgreSQL.
- Ruff check: All checks passed. Ruff format --check: 245 files already formatted.
- mypy: Success, no issues found in 87 source files.
- 부모 ZIP SHA-256: 18e45c994747d4b3e9ab9ae6a75203d65c72dca0a3dcaf0fcd6586770ee7e587. 부모285개 파일 중4개만 수정하고 helper·신규 테스트3개·문서2개를 추가합니다. 변경 목록·전 파일 SHA-256/ZIP CRC/누락과 DemoCoordinator의 변경 메서드가 _fresh/submit/resume_entries뿐임을 패키징 시 검사합니다.
- main은 64e9e7b3d833824c763a2a0f2df1493300744121 유지. 별도 windows-demo-event-inbox-fix-20261030 브랜치로 게시합니다.

합성 거래소 응답·로컬 WebSocket·실제 PostgreSQL에서 실행했습니다. 클라우드에서 인증된 Binance 주문을 실행하지 않았습니다. 수신부터 이미30초 늦은 이벤트나 최신 대표 알림도 오래된 상태는 계속 실패하며 억지로 통과시키지 않습니다. 사용자 Windows의 실제 새 DEMO 검증 성공, 실제 부분 체결/연결 끊김/재시작 수락, Phase4 전체 완료와 LIVE/Phase5 승인은 아직 별도입니다.
