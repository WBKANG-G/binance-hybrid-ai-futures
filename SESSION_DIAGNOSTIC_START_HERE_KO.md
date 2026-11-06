# 주문 없이 개인 스트림을 점검하고 실패 원인을 분류하기

## 이번 기록에서 확인한 것

작업7ad58edd-1b41-4b53-a989-e9f2cea180a0는0.0007 BTC 진입 체결, SL/TP 확인 후 청산(CLOSED/FINISHED_FLAT)을 마쳤지만 session_error=true로 전체 검증에 실패했습니다. 10월30일11:43:59 UTC 보호 확인 뒤11:44:02 UTC 스트림 비신뢰 전환이 있고,11:44:30/40 UTC 청산 확인이 있습니다. 마지막24개 감사 기록에는 새 DEMO_RECONCILIATION_FAILED가 없어 SNAPSHOT_CHANGED 표시를 이번 실패 원인으로 단정할 수 없습니다. 구버전은 실제 세션 예외/이벤트 종류/지연 시간을 보존하지 않았으므로 정확한 원인은 아직 미확정입니다.

이 ZIP은 진단과 주문 없는 연결 점검을 추가합니다. 기존 이벤트 허용 목록·30초 신선도·미래 이벤트 차단·위험/보호/정리 규칙을 바꾸거나 세션 오류를 성공으로 바꾸지 않습니다.

## 1. 기존 Galaxybook의 새 폴더에 설치

ZIP을 windows-demo-session-diagnostic 폴더에 풀고 열어 탐색기 주소창에 cmd를 입력합니다. 경로 끝이 새 폴더인지 확인합니다.

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

기존 프로젝트/DB/export 볼륨과 적용한 일별·주별 기준을 유지합니다. 초기화/down -v/export 생성 재요청/기준 재적용을 하지 않습니다. 새 이미지로 API 컨테이너가 교체되면 기존 메모리 키가 사라지므로 이번 설치 뒤 한 번만 다시 등록합니다.

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.demo_key_session --register
```

DEMO 키와 Secret은 숨김 입력합니다. REGISTERED_IN_PRIVATE_TMPFS면 이후 명령은 재입력이 없습니다. 키 등록이 이미 있다고 나오면 반복/교체하지 말고 상태를 공유합니다.

## 2. 주문 없는 30초 스트림 점검

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.observe_demo_private
```

CMD 경로가 다시 나올 때까지 기다린 뒤 결과만 공유합니다. 키를 채팅에 보내지 않습니다. 이 단계는 복구 승인이나 새 진입 검증을 먼저 요구하지 않습니다. 현재 EMERGENCY/paused를 유지한 채 빈 계정의 연결과 이벤트를 관찰합니다.

정상 결과는 OBSERVATION_FINISHED_NO_ENTRY입니다. 오류이면 FAILED_OR_BLOCKED_NO_ENTRY와 private_session_diagnostic을 확인합니다. 오류를 반복하거나 새 매수를 실행하지 않습니다. 조용한 연결 점검이 통과해도 직전 체결 관찰 오류 원인이 해결됐다는 뜻은 아니며, 다음 실행 경로를 따로 판단합니다.

## 무엇을 바꾸는 점검인가

계정이 로컬에서 일시 정지·ISOLATED BTCUSDT·노출/잔량0·모든 주문 CLOSED인지 확인하고, 기존 lease/fencing을 획득해 다른 실행자와 겹치지 않게 합니다. REST 대조와 WebSocket 연결을 점검하면서 DB 감사 기록과 실제 최신 조회 상태를 남깁니다. 따라서 DB까지 읽기 전용인 명령은 아닙니다. 현재 평가/대조 시각은 실제 새 조회 시각으로 갱신될 수 있지만 과거 영수증의 실제 시각을 자정으로 바꾸거나 기준을 재작성하지 않습니다.

거래소에서는 연결용 listenKey 생성/종료(필요 시 갱신)만 허용합니다. 주문 신호/진입/청산/보호 취소/레버리지 변경은 하지 않습니다. 전용 ObservationClient는 GET 외의 모든 request 요청을 SDK에 전달하기 전에 거부합니다. 원 SDK의 listen_key가 활성 DEMO 설정을 요구하기 때문에 관찰 전용 클라이언트 설정은 세션용으로 활성화하지만, 별도 요청 차단이 거래소 주문/설정 쓰기를 금지합니다. 신규 진입 재개 호출은 없습니다.

## 진단 값

private_session_diagnostic은 이번 세션의 stage/code, 허용된 event_kind 또는 UNKNOWN, 제한된 event_age_ms, ready_callback_ms만 표시합니다. 예: EVENT_TIME_INVALID(처리 시각 기준 오래되거나 미래/잘못된 이벤트 시각), EVENT_UNKNOWN, SOCKET_CLOSED, EVENT_JSON_INVALID, CLOCK_OR_COOLDOWN, DB_LOCK_TIMEOUT, SNAPSHOT_CHANGED 등. 원본 메시지·알 수 없는 이벤트 문자열·URL·서명·키·예외 본문을 저장/출력하지 않습니다. 이벤트 나이는 처리 호출 직전 표본이며 -60초~1시간으로 제한해 진단용으로만 기록하며 기존 신선도 판정에는 전혀 사용하지 않습니다.

실패 시 DEMO_PRIVATE_SESSION_FAILED 감사 이벤트와 last_private_session_failure를 남깁니다. DB 기록이 실패해도 이번 실행의 메모리 진단은 최종 출력에 남깁니다. last_private_session_failure는 마지막 과거 실패일 수 있으므로 이번 출력의 private_session_diagnostic과 구분합니다. 기존 reconciliation_failure_code도 과거 실패 표시가 남을 수 있습니다. 정상 종료 취소는 실패로 새로 기록하지 않으며 실제 세션 오류는 정리 중 취소되더라도 계속 실패입니다.

기존 키 재사용·기간 기준 검토·최초 체결/worker 대기/운영 조회 수정은 포함합니다. 이 단계는 실제 자동 진입 검증 성공·Phase4 완료·서버 상시 운영 또는 LIVE/Phase5 승인이 아닙니다.
