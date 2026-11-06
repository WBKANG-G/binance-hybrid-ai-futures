# Windows DEMO 손절 로컬 조립 실패 시험: 미체결로 검증 미완료

근거: 사용자 CMD 결과 캡처. 2026-10-31 한국 시간.
배포 windows-demo-sl-fault-check-20261031 / d9b3c4a.
ZIP SHA256 900d502dfc0ec716d102bf92b1a172a2e335985a6208ff8a8f41d49ce6d23d88.
작업 ID 5c96345b-f0c6-42cc-8132-534b26546577.

## 실제 출력

- DB/API/web Healthy, 키 private tmpfs RAM 등록 성공
- RESERVED_NOT_YET_SUBMITTED
- quantity/cap 0.0007 / limit_price 77418.70
- stop_price 77031.10 / target_price 78192.90
- sl_fault_check NOT_ACCEPTED
- scope LOCAL_SL_BUILD_FAILURE_ORIGINAL_AUTO_FLATTEN
- fault_injection_count 0 / fault_at_utc null
- entry_fill_scope_verified false
- entries_paused_after_fault false / entry_gate_blocked_after_fault false
- sl_not_sent_proof_verified false
- unexpected_protection_transport_attempts 0
- new_entry_post_count 1 / entry_post_attempts 1
- reduce_only_post_attempts 0
- automatic_exit_observed_before_cleanup false
- fallback_operator_emergency_requested false
- recovered_existing_intent false
- filled_quantity 0.0000 / exit_state NONE
- flat_confirmation VERIFIED_FLAT
- history_and_baselines_preserved true
- failure FAULT_NOT_OBSERVED / failure_stage WAIT_SL_BUILD_FAULT
- completion_failure NONE
- private_session_diagnostic PING/NONE/event_kind NONE
- ready_callback_ms 18754 / received_events 2 / coalesced_events 0
- event_clock_offset_ms 836 / event_clock_rtt_ms 165 / event_clock_wait_ms 0
- event_local_age_ms 6364
- risk_state NORMAL / final_paused true
- final_stream_ready false / final_reconciled false
- orders_enabled false / live_trading_enabled false

## 판정

신규 DEMO 지정가 주문1건을 전송했으나 체결량0이므로 손절 조립에 도달하지 않았고 장애 주입0회로 관찰 창이 끝났다. 손절 실패 대응 통과나 실패로 확대하지 않고, 미체결로 필요한 조건이 충족되지 않은 미수락 결과로 보존한다. 최초 ask 기반 지정가가 체결되지 않은 시장 원인은 이 집계 출력만으로 확정하지 않는다. 실제 주문의 최종 일반 주문 상태는 이 출력에 없으므로 CANCELED/EXPIRED 중 하나로 단정하지 않는다.

종료 정리 및 VERIFIED_FLAT, 이력·기준금 보존, NORMAL/paused를 확인했다. after_fault 계열 false는 장애가 발생한 뒤의 검증을 수행하지 않았다는 뜻이며 최종 paused true와 모순되지 않는다. 비상 상태가 아니므로 별도 비상 복구 승인 명령을 요구하지 않는다. Docker의 Gordon 안내는 앱의 비정상 종료 코드에 따른 일반 안내로, Compose 자체 고장을 입증하지 않는다.

같은 명령 반복 또는 새 UUID로 임의 주문 재시도하지 않는다. 미체결을 피하려고 시장가 신규 진입·수량 증가·가격/관찰 시간 제한 완화를 적용하지 않는다. 손절 로컬 실패 자동 비상 종료의 실제 DEMO 수락 검증은 여전히 미완료다. 실제 거래소 거부/외부 알림/부분체결 보호/Phase4 전체 통과로 확대하지 않는다.
