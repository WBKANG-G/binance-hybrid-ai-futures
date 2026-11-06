# Windows DEMO 미체결 주문 중 WSS 단절·취소·재연결 실제 통과

근거: 사용자가 제공한 CMD 최종 화면. 2026-10-31 한국 시간.
배포: windows-demo-disconnect-drain-fix-20261031 / 8be775d.
ZIP SHA256: 067562df57c011aadb6abda07850dda0942f269dbdf19e5df7c693839b19c31e.
작업 ID: debe90bc-1195-4e1b-92ef-22f41229c64e.

## 실제 결과

- 단계 READY → BLOCKED → RECONNECTED
- disconnect_check OPEN_ORDER_SOCKET_LOSS_CANCEL_RECONNECT_VERIFIED
- scope OPEN_ORDER_LOCAL_SOCKET_CLOSE
- injected_close_count 1 / reconnect_count 1
- disconnect_code SOCKET_CLOSED
- entry_gate_blocked_after_disconnect true
- same_order_observed_before_after true
- cancelled_unfilled_order_verified true
- new_entry_post_count 1 / entry_post_attempts 1
- recovered_existing_intent false
- failure NONE / failure_stage NONE / completion_failure NONE
- flat_confirmation VERIFIED_FLAT
- history_and_baselines_preserved true
- filled_quantity 0.0000
- risk_state NORMAL / final_paused true
- final_stream_ready false / final_reconciled false
- orders_enabled false / live_trading_enabled false
- last_recorded_private_session_failure: RECEIVE / SOCKET_CLOSED

## 실제 시각

- first_session_started_at_utc 2026-10-31T09:04:04.691603+00:00 (한국 시간 18:04:04)
- socket_close_requested_at_utc 2026-10-31T09:04:30.958860+00:00
- socket_closed_at_utc 2026-10-31T09:04:32.963159+00:00
- first_session_completed_at_utc 2026-10-31T09:04:51.735516+00:00
- socket_observed_elapsed_ms 28275 / fault_observation_timeout_seconds 45

단절은 최초 세션 시작 후 약28.3초로 45초 이내에 관측됐다. 최초 세션 정리 완료는 약47.0초 뒤였으며, 관측 타이머와 정리 drain을 분리한 경로로 재연결과 최종 확인을 수행했다. 이 계산은 이번 실행의 시각이며, 이전 실패 실행의 원래 예외 객체를 소급해 확인한 것은 아니다.

## 판정 및 범위

실제 DEMO에서 미체결 주문의 로컬 WSS를 한 번 닫고, 기존 세션의 진입 차단·잔량 취소를 수행했다. 재연결의 전체 REST 대조 후 동일 거래소 주문의 체결0/CANCELED 상태를 확인했고, 같은 의도 재호출이 추가 진입 POST를 만들지 않았다. 최종 flat/NORMAL/paused 및 기존 이력·기준 보존을 확인했다. 마지막 stream/reconciled false는 검증 세션 종료 뒤의 상태다. 보존된 SOCKET_CLOSED는 의도적으로 주입한 단절 기록이며 최종 failure NONE과 구분한다.

이 항목은 실제 통과다. 기존 4e81bbe7-12f8-4e65-848a-f3a928b13a6e는 재연결0의 실패로 보존한다. 이번 검증은 인터넷 전체 단절, 실행 프로세스/호스트/DB 중단, 포지션 보유 중 장애, 실제 부분체결 보호, 장기 무인 운용 또는 Phase4 전체 통과를 의미하지 않는다. 추가 정리나 비상 잠금 해제, 같은 작업 재실행은 필요하지 않다.
