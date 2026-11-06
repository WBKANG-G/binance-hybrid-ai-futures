# Windows DEMO 빈 계좌 개인 WSS 30분 관찰·갱신 실제 통과

근거: 사용자가 제공한 CMD 진행 및 최종 결과 화면. 2026-10-31 한국 시간.
배포: windows-demo-private-stability-check-20261031 / e233c5d.
ZIP SHA256: 904ebf67fa96d7ec473a91b49c7356edcbdb1f38d47dc3b6a4e19806b7d08e7f.
작업 ID: 87723a08-552e-43aa-9d5c-062de037aec8.

## 실제 결과

- private_stability EMPTY_PRIVATE_SESSION_STABILITY_RENEWAL_VERIFIED
- scope EMPTY_ACCOUNT_SINGLE_WSS_SESSION
- target_seconds 1800 / observed_elapsed_ms 1800014
- ready_at_utc 2026-10-31T09:49:10.078862+00:00 (한국 시간18:49:10)
- observation_finished_at_utc 2026-10-31T10:19:09.888857+00:00 (한국 시간19:19:09)
- key_create_success_count 1
- successful_key_renewals 1
- key_close_success_count 1
- successful_read_queries 4392
- received_events 0
- private_session_diagnostic PING / NONE / event_kind NONE
- ready_callback_ms 35 / received_events 0 / coalesced_events 0
- event_clock_offset_ms null / event_clock_rtt_ms null / event_clock_wait_ms 0 / event_local_age_ms null
- failure NONE / failure_stage NONE / completion_failure NONE
- history_and_baselines_preserved true
- risk_state NORMAL / final_paused true
- final_stream_ready false / final_reconciled false
- new_entry_post_count 0 / orders_enabled false / live_trading_enabled false

진행 화면에서 elapsed_seconds1440까지 갱신0, 1500부터1800까지 갱신1을 확인했다. 관찰시간 판정은 monotonic 기준1800.014초이며 위 UTC 출력의 차이를 대신 사용하지 않는다.

## 판정과 범위

실제 DEMO에서 주문 없는 단일 개인 WSS 세션을30분 관찰하고, 원래 세션의 약25분 listenKey 갱신 API 성공 응답 및 종료 시 키 삭제를 확인했다. 수신 이벤트0은 빈 계좌에서 가능한 결과이며, PING/오류NONE 및 원래 ping/pong·REST 대조 경로와 함께 판정한다. 받은 주문/체결 이벤트가 있었다거나 그 원문을 저장했다고 주장하지 않는다. successful_read_queries는 일반 request GET 성공 횟수이며 public time/metadata까지 더한 전체 HTTP 횟수는 아니다.

기존 이력·기준 보존, 최종 빈 계좌 확인, NORMAL/paused 종료를 통과했다. 마지막 stream/reconciled false는 세션을 닫은 후의 상태다. 추가 정리·비상 잠금 해제·같은 작업 재실행은 필요하지 않다.

30분 단일 세션 및 갱신 API 성공 확인 범위다. 최초 listenKey 만료 시한 이후의 연결 유지,24시간 무인 운용, 원문 WSS 영구 저장, 실제 부분체결 보호, 보유 주문/포지션 Worker 강제 종료, 전체 호스트/DB 장애 또는 Phase4 전체 통과로 확대하지 않는다. 이전 실패 기록은 그대로 유지한다.
