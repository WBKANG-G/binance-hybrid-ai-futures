# Windows DEMO 로컬 TP 조립 실패·SL 유지·진입 차단·운영자 비상 정리 통과

근거: 사용자가 제공한 CMD 최종 화면. 2026-10-31 한국 시간.
배포: windows-demo-tp-fault-check-20261031 / dc15b84.
ZIP SHA256: dd0f177faf7934192944bf9c5be67d99da3e9142d47bee51713f413fb277f338.
작업 ID: 426dd7a2-805b-4576-a1f4-85313a7c4f6c.

## 실제 출력

- 예약 수량 및 cap 0.0007 BTC
- 지정가 77388.70 / 손절 77001.70 / 익절 가정 78173.90
- 단계 RESERVED_NOT_YET_SUBMITTED → TP_NOT_SENT_ENTRIES_BLOCKED_SL_RETAINED
- tp_fault_check TP_BUILD_FAILURE_PAUSE_SL_RETAINED_OPERATOR_FLAT_VERIFIED
- scope LOCAL_TP_BUILD_FAILURE_AFTER_SIGNED_STOP
- fault_injection_count 1
- fault_at_utc 2026-10-31T10:58:45.179933+00:00 (한국 시간19:58:45)
- signed_stop_before_fault_verified true
- entries_paused_after_fault true / entry_gate_blocked_after_fault true
- tp_not_sent_proof_verified true / unexpected_tp_transport_attempts 0
- new_entry_post_count 1 / entry_post_attempts 1
- reduce_only_post_attempts 1 / operator_emergency_requested true
- recovered_existing_intent false / filled_quantity 0.0007
- exit_state FILLED / flat_confirmation VERIFIED_FLAT
- history_and_baselines_preserved true
- failure NONE / failure_stage NONE / completion_failure NONE
- private_session_diagnostic HEARTBEAT / NONE / event_kind NONE
- ready_callback_ms 21988 / received_events 5 / coalesced_events 0
- event_clock_offset_ms null / event_clock_rtt_ms null / event_clock_wait_ms 0 / event_local_age_ms null
- risk_state EMERGENCY / final_paused true
- final_stream_ready false / final_reconciled false
- orders_enabled false / live_trading_enabled false

## 판정과 후속 상태

실제 DEMO의 최소 cap 진입 체결 뒤, 같은 SL을 signed GET으로 확인하고 로컬 TP 조립에만 실패를1회 주입했다. 원래 코드의 NOT_SENT/LOCAL_VALIDATION_FAILED 증거, 신규 진입 차단 및 SL 보호 유지가 확인됐다. 이후 검증 도구가 명시적 운영자 비상 정리를 요청하고 원래 reduce-only 종료1회 및 FILLED/VERIFIED_FLAT까지 확인했다. 과거 이력·기준 보존도 통과했다.

이는 로컬 TP 조립 실패에 대한 실제 DEMO 대응 통과다. 거래소의 실제 TP 거부 응답이나 SL 생성 실패를 직접 일으킨 것은 아니며, TP 실패가 자동 즉시 청산을 일으킨다고 설명하지 않는다. TP가 정상 등록·체결된 것으로 분류하거나 부분체결 전체 보호 통과로 분류하지 않는다.

EMERGENCY는 사용한 비상 정리 명령이 유지하는 잠금이다. 다음 단계는 기존 읽기 전용 review_demo_recovery로 현재 포지션/주문/요청/위험을 재검토하는 것이다. 이 출력만으로 잠금을 자동 해제하거나 기존 token을 재사용하지 않는다. 같은 주문/장애를 재실행하지 않는다. Phase4 전체/LIVE/Phase5 통과로 확대하지 않는다.
