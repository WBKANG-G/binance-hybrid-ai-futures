# 세션 오류 분류와 주문 없는 점검: 검증 기록

## 실제 증거와 미확정 사항

사용자 작업7ad58edd-1b41-4b53-a989-e9f2cea180a0는 FULL_FILL0.0007/stop_verified=true/target_verified=true/FINISHED_FLAT/CLOSED였지만 session_error=true와 DEMO_SESSION_OR_QUERY_FAILED로 전체 검증에 실패했습니다. completion_failure/database_failure는 NONE, 마지막 불일치 필드는[],추가 최초 체결 처리는 사용하지 않았습니다. 로컬 감사 마지막24개는11:43:59 UTC 보호 확인 뒤11:44:02 STREAM_UNTRUSTED를 보여주며 새로운 DEMO_RECONCILIATION_FAILED는 없습니다. SNAPSHOT_CHANGED 필드만으로 현재 실패를 확정할 수 없습니다. 원래 이벤트/예외가 기록되지 않아 정확한 원인은 아직 알 수 없습니다.

코드를 보면 현재 이벤트 종류/시각 검사 또는 연결 실패가 일반 DEMO 세션 실패로 합쳐집니다. 오래 대기한 이벤트가 원인일 가능성은 합성 재현으로만 확인했으며 사용자 실제 원인으로 단정하지 않습니다. 실패를 성공으로 바꾸거나 신선도 허용 시간을 늘리지 않습니다.

## 변경

기본값이 있는 PrivateSessionDiagnostic/last_private_session_failure 모델, 허용 목록 분류 helper, 세션 생성/연결/대조/콜백/수신/파싱/이벤트/핑/갱신 단계 계측, 실패 감사 기록과 최종 출력 필드를 추가했습니다. 실제 예외를 재시도하지 않으며 원본 예외/URL/메시지/알 수 없는 이벤트명/키 값은 밖으로 내보내지 않습니다. 이벤트 나이와 콜백 시간은 진단 범위로 제한합니다. 진단 값은 기존 판정에 영향을 주지 않습니다. 오류는 원래와 같이 연결을 비신뢰 처리하고 정리를 진행합니다. 정상 취소는 실패 진단을 만들지 않습니다.

새 observe_demo_private는 빈 일시 정지 ISOLATED BTCUSDT 계정만 lease/fencing하에 관찰합니다. NORMAL과 EMERGENCY의 정지 상태를 허용하지만 복구/재개 호출은 없습니다. ObservationClient는 모든 GET 외 request를 금지합니다. 연결 키 생명주기만 원 SDK의 전용 listen_key로 처리합니다. SDK listen_key의 기존 DEMO 활성 조건 때문에 관찰 전용 설정은 활성이고, 주문/계정 변경은 별도 클래스의 전송 차단으로 금지합니다. 현재 상태·감사를 DB에 기록하므로 DB 읽기 전용이라고 주장하지 않습니다. 같은 계정의 활성 실행자와 경합하면 lease 획득에서 차단됩니다.

기존 demo.py의 이벤트 허용 목록/30초 판정/connected/disconnect/reconcile/주문·위험·보호/청산 구현, account_risk/coherent_snapshot·SDK·스냅샷·저장/fencing·정책·기준·키 보관·Docker/Compose·export는 변경하지 않았습니다. demo.py에는 진단 인스턴스 초기화와 import만 추가했습니다. run_demo CLI와 기본 check_private 계약도 그대로입니다. 키 보관과 모델의 추가 기본값은 기존 원장과 호환되며 DB 스키마 변경이 없습니다.

## 테스트

새 집중 검사51개 통과(3.87초). 허용된 분류/알 수 없는 이벤트 문자열 제거/나이 제한, DB 오류 고정 분류, 알 수 없는 이벤트·30초 초과·미래 이벤트·잘못된 JSON/객체·크기 초과·소켓 종료·REST 불일치·세션 생성 실패의 실제 로컬 WebSocket/PG 감사 기록을 확인합니다. 정상 취소에서 실패가 만들어지지 않는지, 세션 콜백 동안31초 지난 이벤트가 그대로 거부되고 EVENT_TIME_INVALID/31000ms로 식별되는지도 검사합니다. 이는 실제 사용자 원인 확정이나 큐 처리 수정의 증명이 아닙니다.

관찰 클라이언트가 POST/PUT/PATCH/DELETE와 주문/보호/레버리지 경로를 SDK 이전에 차단하고 GET만 원래 SDK로 전달하는지 검사합니다. 실제 PG의 일시 정지 EMERGENCY에서 관찰이 완료되고 기준/고점/요청/잠금이 유지되며 주문 신호가 없고 listenKey POST/DELETE만 발생하는지 확인합니다. 로컬 노출이 있으면 점검을 차단합니다.

- 전체 pytest: 849 passed in 186.76s (0:03:06). TEST_DATABASE_URL은 실제 로컬 PostgreSQL.
- Ruff check: All checks passed. Ruff format --check: 239 files already formatted.
- mypy: Success, no issues found in 86 source files. docker compose config --quiet 통과.
- 부모 ZIP SHA-256: 9f096eebf0aad71ef70589e2c69dabdf5babc56cb5ec55118ba3cedc29dd8858. 기존278개 파일에서 모델/코디네이터 초기화/세션/검증 출력4개를 수정하고 helper·관찰 CLI·테스트3개·문서2개를 추가합니다. 패키징에서 변경 목록·전 파일 SHA-256·ZIP CRC/누락을 검사합니다.
- main은 64e9e7b3d833824c763a2a0f2df1493300744121 유지. 별도 windows-demo-session-diagnostic-20261030 브랜치로 게시합니다.

시험은 합성 거래소 응답+로컬 WebSocket+실제 PostgreSQL입니다. 클라우드에서 인증된 Binance 호출/새 주문은 하지 않았습니다. 사용자 Windows의 새 진단 점검 결과를 먼저 받아야 하며, 조용한 연결 점검 통과로 체결 후 관찰 오류가 해결됐다고 주장하지 않습니다. 실제 새 자동 매매 수락과 Phase4 완료·LIVE/Phase5 전환은 별도입니다.
