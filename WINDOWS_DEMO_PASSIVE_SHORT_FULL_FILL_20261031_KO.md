# Windows DEMO SHORT PASSIVE 전량 체결 실제 검증 통과

근거: 사용자가 제공한 CMD 최종 화면. 2026-10-31 한국 시간.
배포: windows-demo-restart-handoff-fix.

- 작업 ID: 095b9d5c-b79d-4731-9d64-971e4ffde7d0
- 방향 SHORT / 진입 방식 PASSIVE / 수량 및 cap 0.0007 BTC
- 지정가 77389.80 / 손절 77777.50 / 익절 76615.90
- validation FINISHED_FLAT
- entry_filled true / filled_quantity 0.0007
- entry_state FILLED / position_state CLOSED / entry_outcome FULL_FILL
- stop_verified true / target_verified true
- filled_protection_and_flat_verified true
- session_error false / session_failure NONE
- validation_stage OBSERVATION / completion_stage FLAT_CONFIRMATION
- completion_failure NONE / completion_database_failure NONE
- private diagnostic RECEIVE / NONE
- ready_callback_ms 19406 / received_events 6 / coalesced_events 4
- event_clock_offset_ms 751 / event_clock_rtt_ms 150
- event_clock_wait_ms 0 / event_clock_local_age_ms 176
- initial_fill_settled false / live_trading_enabled false

SHORT 전량 체결 후 보호 주문 확인 및 최종 flat 검증 통과. 손절·익절 자체의 트리거 체결, 부분체결 보호, 실행 컨테이너 자체 중단·복구 또는 Phase4 전체 완료를 뜻하지 않는다.

last_reconciliation_mismatch_fields 및 reconciliation_settle_reason FILL_PROOF_MISMATCH는 ledger에 보존되는 진단 값이다. 일반 coherent 성공 경로는 이를 지우지 않는다. 이번 출력의 session_failure NONE, session_error false, 보호 및 flat 검증 true와 구분한다.

이번 최종 출력에는 risk_state가 없으므로 NORMAL이라고 추정하지 않는다. 후속 상태 확인으로 잠금 유지 여부를 판단한다. 기존 실패 이력은 수정하지 않는다.
