# Windows 빈 계좌 유휴 DB 정상 재시작·장부 보존 통과

근거: 사용자 CMD 캡처. 배포 windows-demo-empty-db-restart-20261031 / cf0a168.
ZIP SHA256 e6e970230db76cd97f035d39d289afa549b48319f050c92132ecc70b4d847cc3.
작업 ID 98e59d82-c594-4ceb-936e-fa1a81ee83eb.

## 실행 및 실제 결과

prepare의 PREPARED_EMPTY_NO_ORDERS와 leader_released true를 확인한 뒤, 동일 프로젝트에서 restart db → up -d --wait → 같은 UUID의 --verify를 순서대로 실행했다. API/web/DB 모두 Healthy.

- db_restart_check EMPTY_DB_RESTART_HISTORY_PRESERVED_VERIFIED
- scope EMPTY_IDLE_DB_SERVER_RESTART
- history_and_baselines_preserved true
- postgres_restart_observed true
- server_started_before_utc 2026-10-31T08:01:01.587943+00:00
- server_started_current_utc 2026-10-31T14:48:30.252136+00:00 (한국 시간23:48:30)
- fence_before69 / fence_current70
- exchange_empty_verified true
- risk_state NORMAL / paused true
- entry_signal_supplied false / new_entry_post_count0
- orders_enabled false / live_trading_enabled false
- read_diagnostic POOLED_HTTPS
- audit_http_attempts22 / audit_connection_attempts1 / audit_reused_requests21
- audit_server_close_responses0 / audit_transport_elapsed_ms1339
- audit_last_route OTHER_GET / signed_get_count22 / clock_probe_count0
- leader_released true

## 판정과 한계

실제 Windows PostgreSQL 컨테이너의 유휴 재시작 전후에 전체 장부 비교 해시와 원격 empty 상태 해시가 일치했고, PostgreSQL 시작 시각 증가 및 다음 리더 펜스가 정확히1 증가한 것이 확인됐다. 이력·기준금·회계·기존 운영 표식·UNKNOWN/NOT_SENT 증거는 보존됐다. 신규 주문·포지션이 없고 NORMAL/paused를 유지했다. 검증 후 리더 반환도 확인했다. 전송 측정1.339초는 확인 문맥의 측정이며 명령 전체 소요 시간으로 단정하지 않는다.

전체 감사 이벤트/outbox 내용 해시, 작업 중 DB 단절, worker 강제 종료, 전원 손실·호스트 재부팅, 포지션/주문이 있는 상태의 DB 장애, WSS 재접속, 실제 부분체결 보호까지 통과한 것으로 확대하지 않는다. 이번 범위는 빈 계좌의 유휴 DB 정상 재시작 및 장부 보존이다.

추가 비상 복구 승인은 필요하지 않다. 같은 prepare/verify 또는 재시작 명령을 반복하지 않는다. 신규 진입 일시정지를 유지한다. 이전 실제 SL 로컬 실패 통과/복구 승인, 응답 유실 시험 NOT_ACCEPTED 및 후속 종료 확인 수정 통과 기록은 그대로 보존한다. Phase4 전체 완료·LIVE·Phase5 전환으로 확대하지 않는다.
