# Windows DEMO PASSIVE 전량 체결 실제 검증

사용자 CMD 최종 화면 근거, 2026-10-31 한국 시간. 배포 windows-demo-restart-handoff-fix.

- 작업 ID: 648b31e2-570c-4ac5-99a6-38b6dbb8bcee
- LONG / PASSIVE / BTCUSDT / 수량 및 cap 0.0007
- 지정가 77396.00 / 손절 77005.10 / 익절 78170.00
- validation FINISHED_FLAT / entry_outcome FULL_FILL
- filled_quantity 0.0007 / entry_state FILLED / position_state CLOSED
- stop_verified true / target_verified true
- filled_protection_and_flat_verified true
- session_error false / session_failure NONE
- validation_stage OBSERVATION / completion_stage FLAT_CONFIRMATION
- completion_failure NONE / completion_database_failure NONE
- private diagnostic PING / NONE, received_events 6 / coalesced_events 5
- ready_callback_ms 22223 / event_clock_offset_ms 753 / event_clock_rtt_ms 157
- event_clock_wait_ms 0 / event_clock_local_age_ms 147
- initial_fill_settled false / live_trading_enabled false

전량 체결 후 보호 주문 확인 및 최종 flat 검증 통과. 부분체결 보호 성공의 증거로 사용하지 않는다. 손절 또는 익절 주문 자체가 트리거되어 청산됐다는 의미로 확대하지 않는다.

화면의 last_reconciliation_mismatch_fields 및 reconciliation_settle_reason FILL_PROOF_MISMATCH는 ledger에 보존되는 진단 필드다. 일반 coherent 성공 경로는 이 필드를 지우지 않으며, 이번 실행의 session_failure는 NONE이고 보호 및 flat 통과 값은 true다. 해당 필드만으로 이번 실행 실패라고 판정하지 않는다.

최종 risk_state는 이번 최종 출력에 없으므로 NORMAL로 추정하지 않는다. 후속 실행 전 현재 상태 확인이 필요하다. 이전 실제 실패 결과를 변경하지 않는다.
