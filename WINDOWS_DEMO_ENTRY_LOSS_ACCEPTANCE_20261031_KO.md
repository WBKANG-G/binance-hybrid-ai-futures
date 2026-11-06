# Windows DEMO 로컬 SDK 진입 ACK 유실·동일 ID 복구·중복 진입 방지·flat 전체 통과

근거: 사용자 CMD 캡처. 배포 windows-demo-empty-db-restart-20261031 / cf0a168 (종료 확인 수정6e88c76 포함).
ZIP SHA256 e6e970230db76cd97f035d39d289afa549b48319f050c92132ecc70b4d847cc3.
작업 ID a5ba93d9-85e3-4066-a03e-12a2da07185f.

## 실제 출력

- 예약 quantity/cap0.0007 / limit_price77516.10
- stop_price77121.20 / target_price78291.30
- 단계 RESERVED_NOT_YET_SUBMITTED → PERSISTED_UNKNOWN_SAME_ID_QUERY_RECOVERED
- entry_loss_check ENTRY_ACK_LOSS_SAME_ID_NO_DUPLICATE_FLAT_VERIFIED
- scope LOCAL_SDK_ENTRY_ACK_LOSS_AFTER_SUCCESS_RESPONSE
- response_drop_count1 / unknown_intent_persisted true
- entry_gate_blocked_while_unknown true / same_id_signed_query_verified true
- same_id_query_count19 / local_order_recovered_before_cleanup true
- new_entry_post_count1 / entry_post_attempts1 / reduce_only_post_attempts1
- operator_emergency_requested true / recovered_existing_intent false
- filled_quantity0.0007 / exit_state FILLED / flat_confirmation VERIFIED_FLAT
- history_and_baselines_preserved true
- failure NONE / failure_stage NONE / completion_failure NONE
- private_session_diagnostic READY_CALLBACK/NONE/event_kind NONE
- event_age_ms null / ready_callback_ms2301 / event_receive_age_ms null
- received_events6 / coalesced_events0
- event_clock_offset_ms null / event_clock_rtt_ms null / event_clock_wait_ms0 / event_local_age_ms null
- risk_state EMERGENCY / final_paused true
- final_stream_ready false / final_reconciled false
- orders_enabled false / live_trading_enabled false

## 판정

실제 DEMO에서 SDK가 성공한 진입 ACK의 coordinator 전달만 로컬에서1회 유실시켰다. 원래 코드가 UNKNOWN을 영속 기록하고 신규 진입을 차단했으며 같은 client ID와 거래소 주문 ID의 signed GET으로 주문을 복구했다. 신규 진입1건으로 중복 전송이 없었다. 체결량0.0007을 기존 운영자 비상 정리 경로로 종료1건 처리하고 FILLED/VERIFIED_FLAT를 확인했다. 기존 이력·기준금·원래 UNKNOWN 증거를 보존했고 현재 시험의 실패/정리 오류는 모두 NONE이다. 종료 확인 수정 후 이번 새 작업의 전체 수락 조건이 실제 Windows 통과했다.

실제 네트워크 timeout/응답 패킷 손실을 일으킨 것으로 확대하지 않는다. 이전 작업9be80b67의 NOT_ACCEPTED 및 후속 일반 오류/별도 종료 확인·복구 기록은 그대로 보존하며 소급 변경하지 않는다. 이번 성공은 별도 새 의도의 통과다. 실제 부분체결 보호/주문 또는 포지션 worker 강제 종료/실제 거래소 거부/외부 알림/Phase4 전체/LIVE/Phase5 완료로 확대하지 않는다.

최종 EMERGENCY/paused는 원래 비상 정리 경로의 잠금이다. 다음은 인자 없는 review_demo_recovery로 현재 전체 요청·포지션·활성 일반/Algo 주문·기간/리스크를 읽기 전용 검토한다. 현재 시점이 일간 경계를 넘었다면 기간 기준금 차단도 그대로 존중하며 우회/초기화하지 않는다. 기존 토큰 재사용이나 복구 적용, 동일 시험 재실행은 하지 않는다.
