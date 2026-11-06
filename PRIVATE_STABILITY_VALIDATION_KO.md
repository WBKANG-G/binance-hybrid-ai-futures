# 빈 계좌 개인 WSS 연속 관찰·listenKey 갱신 도구 검증

부모 ZIP SHA256: aff2c645855ab0b2ef127a3c00194b1d811696af95fce3671262db7ac5acdc96.
기존 manifest 파일356개를 바이트 그대로 보존한다. 새 실행 모듈/CLI, 통합/단위 테스트, 안내/검증 문서, 최근 실제 빈 계좌 Worker 복구 통과 문서만 추가한다.

## 동작

CLI는 --observe-30-minutes 및 UUID를 요구하고 관찰 시간을 바꾸는 옵션이 없다. LIVE는 키 읽기 전에 거부한다. NORMAL/paused·빈 계좌·ISOLATED BTCUSDT 2x와 초기 거래소 상태를 확인한다. 시작 marker 또는 결과 marker가 있는 UUID는 최초 세션 전에 거부한다.

StabilityClient는 기존 ObservationClient를 상속하므로 일반 request의 GET 이외가 차단된다. request 또는 listen_key 호출이 기존 SDK 검증을 통과해 반환된 뒤에만 성공 카운트를 올린다. POST/PUT/DELETE listenKey 수명 관리는 기존 코드 그대로다. SDK·기존 session·coordinator·서명·시계·리스크·보호·청산·키 캐시·기준금·Compose·exports 및 이전 테스트는 수정하지 않는다.

원래 session 한 번만 실행한다. 연결 준비 대기는45초이며, callback 실패는 완료 신호보다 먼저 보존한다. 준비 완료 후 monotonic1800초를 관찰하며1분마다 안전한 진행 정보를 출력한다. 추가 진행 점검은 DB read이고 별도 SDK 트래픽을 만들지 않는다. 원래 session의5초 inbox 대기/ping/REST 대조, heartbeat/lease 갱신,25분 listenKey PUT 경로를 그대로 사용한다. 수신 실패나 예상보다 이른 session 종료, 이력·기준 변화는 실패다.

정상 관찰 종료나 오류 때 원래 session의 finally를 drain_session으로 끝까지 기다려 DB worker/정리를 겹치지 않는다. 성공에는 단일 생성/성공 갱신1회 이상/단일 삭제,1800초 관찰, 세션 오류 없음, 최종 거래소 빈 상태, 기존 이력·기준 보존과 NORMAL/paused/stream false/reconciled false가 모두 필요하다. 성공 갱신 횟수는 기존 SDK가 갱신 API 성공 응답을 확인한 횟수이며, 최초 listenKey 만료 기한을 넘겨 연결이 유지되는 것까지 검증한 값은 아니다. 키 삭제 실패를 원래 session이 내부에서 소비하더라도 성공 카운트가0이면 통과하지 않는다. 최종 fenced 결과 감사 쓰기 실패도 성공을 차단한다. CLI는 마지막 lease release 실패도 BLOCKED로 처리한다.

단계별 진단은 기존 PrivateSessionDiagnostic의 허용된 필드만 포함한다. successful_read_queries는 일반 request GET의 성공 횟수이며 public metadata/time 호출까지 합친 전체 HTTP 횟수는 아니다. 실패 시 elapsed 값은 마지막 관찰 확인 지점이고 observation_finished_at_utc는 관찰시간 완료 시에만 기록된다. 어떤 미확인 결과도 성공으로 추정하지 않는다.

## 검증

집중 테스트: 22 passed in 20.10s. Ruff check/format 및 mypy100소스 통과.
전체 테스트: 1117 passed in 244.09s (0:04:04). Ruff check/format 통과, mypy100소스 통과.

통합 테스트는 실제 PostgreSQL 격리 schema와 localhost WSS 서버, synthetic signed SDK 응답을 사용한다. 원래25분 갱신 경로는 fixture 시각을 각 원래 sync_clock 호출 전에10초씩 진행하고 실제 ACCOUNT_UPDATE 모양의 이벤트를 로컬 WSS로 보내 실행했다. lease/시계/이벤트 검증은 원래 구현을 그대로 통과한다. 로컬 관찰 벽시계5초 동안 갱신 경로 및 최종 키 삭제를 검사한 것이며, 실제 거래소30분 관찰을 클라우드에서 수행한 것으로 주장하지 않는다. Windows는 실제 monotonic1800초를 기다린다.

새 테스트는 원래 갱신 성공, 갱신 없음 거부, 잘못된 이벤트 실패·원문 비노출, PUT HTTP 실패의 미집계, DELETE 실패 시 거부, 감사 기록 실패, 기준 변화 거부, 기존 시작 UUID 및 준비 marker 없는 실패 결과 UUID 재실행 거부, 준비 timeout/키 정리, CLI 고정 시간/옵트인/LIVE 거부, 일반 order/algo/leverage mutation 전송 차단을 검사했다.

## 마지막 실제 확인 상태

b4cceb33-35bd-4430-b5be-65a49924d9ba: EMPTY_WORKER_SIGKILL_FENCED_RECOVERY_VERIFIED. 자식PID46/-9, 자연 만료59699ms, fence56→57, 경쟁 리더/이전 소유자 effect·renew 차단, 재대조 전 gate 차단, 새 WSS/REST 준비, 이력/기준 보존, NORMAL/paused/leader release, 신규 진입0을 실제 확인했다.

새30분 실제 DEMO 검증은 아직 실행 전이다. 이전 실제 부분체결 실패를 통과로 바꾸지 않는다. 빈 계좌 리더 복구, 미체결 주문 WSS 단절/취소/재연결, 미체결 API 재시작 및 LONG/SHORT 전량 체결·보호·flat은 기존 실제 증거로 구분한다. 실제 부분체결 보호, 보유 주문/포지션 Worker 강제 종료, 호스트/DB 장애 및 장기 운용은 별도 미완료이며 Phase4 전체/LIVE/Phase5 통과로 확대하지 않는다.
