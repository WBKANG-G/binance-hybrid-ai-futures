# Windows DEMO 손절 로컬 조립 실패·진입 차단·원래 자동 비상 종료 통과

근거: 사용자가 제공한 CMD 결과 캡처. 배포 windows-demo-flat-confirmation-fix-20261031 / 6e88c76.
ZIP SHA256 d738f8a5dbcbe8cc285dc2df779e7658164647cc654534a9cdccd48a80adda93.
작업 ID 77db044e-1bcd-4358-80e6-df9730630213.

## 실제 출력

- 예약 quantity/cap 0.0007 / limit_price77442.30
- stop_price77054.90 / target_price78216.80
- 단계 RESERVED_NOT_YET_SUBMITTED → SL_NOT_SENT_GATE_BLOCKED_AUTO_EXIT_FILLED
- sl_fault_check SL_BUILD_FAILURE_GATE_AUTO_FLAT_VERIFIED
- scope LOCAL_SL_BUILD_FAILURE_ORIGINAL_AUTO_FLATTEN
- fault_injection_count1
- fault_at_utc 2026-10-31T14:23:07.751365+00:00 (한국 시간23:23:07)
- entry_fill_scope_verified true
- entries_paused_after_fault true / entry_gate_blocked_after_fault true
- sl_not_sent_proof_verified true
- unexpected_protection_transport_attempts0
- new_entry_post_count1 / entry_post_attempts1 / reduce_only_post_attempts1
- automatic_exit_observed_before_cleanup true
- fallback_operator_emergency_requested false
- recovered_existing_intent false
- filled_quantity0.0007 / exit_state FILLED / flat_confirmation VERIFIED_FLAT
- history_and_baselines_preserved true
- failure NONE / failure_stage NONE / completion_failure NONE
- private_session_diagnostic EVENT/NONE/event_kind ORDER_TRADE_UPDATE
- event_age_ms1413 / ready_callback_ms22812 / event_receive_age_ms178
- received_events8 / coalesced_events7
- event_clock_offset_ms847 / event_clock_rtt_ms158 / event_clock_wait_ms0
- event_local_age_ms513
- risk_state EMERGENCY / final_paused true
- final_stream_ready false / final_reconciled false
- orders_enabled false / live_trading_enabled false

## 판정

실제 DEMO에서 최소 cap 진입 체결 뒤 같은 SL의 로컬 조립에 ValueError를1회 주입했다. 원래 coordinator가 NOT_SENT/LOCAL_VALIDATION_FAILED 증거와 EMERGENCY/paused 및 신규 진입 게이트 차단을 기록하고, 도구의 후속 정리가 시작되기 전에 원래 reduce-only 비상 종료를 수행한 것이 확인됐다. 진입1건/종료1건, 보호 전송 시도0, 종료 FILLED/VERIFIED_FLAT 및 이력·기준금 보존이 모두 통과했다. 후속 운영자 비상 정리는 요청하지 않았다.

이는 로컬 SL 조립 실패에 대한 실제 DEMO 대응 통과이며 거래소의 실제 SL 거부 응답이나 외부 긴급 알림 전달까지 입증하지 않는다. 이전 미체결 작업5c96345b는 주입0회/미수락 기록으로 별도 보존한다. 이번 결과로 이전 응답 유실 시험의 NOT_ACCEPTED나 실제 부분체결 보호 미완료를 바꾸지 않는다. Phase4 전체/LIVE/Phase5 완료로 확대하지 않는다.

최종 EMERGENCY/paused는 원래 위험 대응 잠금으로 유지됐다. 다음은 인자 없는 review_demo_recovery로 최신 포지션/활성 일반·Algo 주문/전체 요청/기간·리스크를 읽기 전용으로 검토하는 것이다. 아직 복구 승인은 적용하지 않았고 이전 토큰을 재사용하지 않는다. 같은 장애·진입 시험을 다시 실행하지 않는다.
