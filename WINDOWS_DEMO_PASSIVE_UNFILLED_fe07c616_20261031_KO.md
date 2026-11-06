# Windows DEMO PASSIVE 추가 1회 시도: 미체결 정상 취소·flat

근거: 사용자가 제공한 CMD 최종 화면. 2026-10-31 한국 시간.
배포: windows-demo-private-stability-check.
작업 ID: fe07c616-cf71-4ed9-b972-14c63b67d141.

- LONG / PASSIVE / 수량 및 cap 0.0007 BTC
- 지정가 77447.70 / 손절 가정 77060.40 / 익절 가정 78223.00
- validation FINISHED_FLAT
- entry_filled false / filled_quantity 0.0000
- entry_state CANCELED / position_state CLOSED / entry_outcome UNFILLED
- stop_verified false / target_verified false
- filled_protection_and_flat_verified false
- session_error false / validation_stage OBSERVATION / session_failure NONE
- private_session_diagnostic HEARTBEAT / NONE / event_kind NONE
- ready_callback_ms 18740 / received_events 2 / coalesced_events 0
- event_clock_offset_ms 787 / event_clock_rtt_ms 146 / event_clock_wait_ms 0 / event_local_age_ms 5812
- completion_stage FLAT_CONFIRMATION
- completion_failure NONE / completion_database_failure NONE
- initial_fill_settled false / live_trading_enabled false

실제 결과는 미체결 진입의 취소와 최종 flat 완료다. 체결 수량0이므로 체결분 SL/TP 검증 값이 false인 것은 예상된 결과다. 부분체결이나 체결분 보호 성공으로 분류하지 않는다. 사용자가 요청한 추가1회 시도는 완료했으며 임의 반복 주문을 하지 않는다.

출력의 last_reconciliation_mismatch_fields 및 reconciliation_settle_reason FILL_PROOF_MISMATCH는 일반 coherent 성공 경로에서 지우지 않는 기존 ledger 진단 필드다. 이번 시도의 session_failure NONE/session_error false/정리 및 DB 오류 NONE과 구분한다. 이 필드만으로 새 실패를 확정하지 않는다.

최종 출력에는 risk_state가 없으므로 NORMAL이라고 추정하지 않는다. 최종 flat/CLOSED는 확인됐으며 후속 주문은 현재 상태를 확인하거나 기존 진입 가드를 통과해야 한다. 이전 실제 부분체결 실패와 이후 FULL_FILL 통과 기록을 수정하지 않는다. 부분체결 보호의 실제 통과는 여전히 미완료다.
