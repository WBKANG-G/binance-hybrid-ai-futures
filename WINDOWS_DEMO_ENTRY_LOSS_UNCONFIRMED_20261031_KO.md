# Windows DEMO 진입 응답 유실: 동일 ID 복구 확인, 종료 미확정

근거: 사용자 CMD 캡처. 배포 windows-demo-entry-response-loss-20261031 / 0e03d75.
ZIP SHA256 c3864f63eeb8a5542c331a0353c142dfdf5d29c5a6d1c3b91ccb8edb27c4247d.
작업 ID 9be80b67-25d4-43d6-b18b-7d12fdd9cd0e.

- quantity/cap 0.0007 / limit 77536.00 / stop 77141.00 / target 78311.40
- 단계 RESERVED_NOT_YET_SUBMITTED → PERSISTED_UNKNOWN_SAME_ID_QUERY_RECOVERED
- entry_loss_check NOT_ACCEPTED
- scope LOCAL_SDK_ENTRY_ACK_LOSS_AFTER_SUCCESS_RESPONSE
- response_drop_count1 / unknown_intent_persisted true
- entry_gate_blocked_while_unknown true / same_id_signed_query_verified true
- same_id_query_count15 / local_order_recovered_before_cleanup true
- new_entry_post_count1 / entry_post_attempts1 / reduce_only_post_attempts1
- operator_emergency_requested true / recovered_existing_intent false
- filled_quantity0.0007 / exit_state ACKNOWLEDGED
- flat_confirmation UNKNOWN / history_and_baselines_preserved true
- failure FLAT_NOT_CONFIRMED / failure_stage FINAL_STATE
- completion_failure DEMO_SESSION_OR_QUERY_FAILED
- private_session_diagnostic READY_CALLBACK/NONE/event_kind NONE
- ready_callback_ms2296 / received_events6 / coalesced_events0
- event clocks/age null / event_clock_wait_ms0
- risk_state EMERGENCY / final_paused true
- final_stream_ready false / final_reconciled true
- orders_enabled false / live_trading_enabled false

동일 ID 조회 복구와 중복 진입 방지는 확인됐으나 전체 시험은 종료 미확정으로 미수락이다. ACKNOWLEDGED는 종료 체결 또는 flat 증거가 아니다. final_reconciled true만으로 현재 포지션0을 단정하지 않는다. completion_failure의 구체적 원인은 이 집계 출력만으로 확정하지 않는다.

새 의도/시험/비상 종료를 다시 실행하지 않는다. EMERGENCY/paused를 유지한다. 다음 단계는 기존 run_demo --confirm-flat으로 기존 종료를 조회하고 정합한 flat을 확인하는 것이다. 이 경로는 신규 진입·종료 POST를 하지 않으며, flat 증명 후 남은 보호 주문이 있을 때만 취소한다. 원래 이력/기준금/UNKNOWN 증거는 보존한다. 검토/복구 승인과 Phase4 전체 완료는 보류한다.
