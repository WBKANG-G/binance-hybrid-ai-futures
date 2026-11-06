# Windows DEMO 미체결 주문 중 API 재시작 실제 검증 통과

근거: 사용자가 제공한 A/B CMD 최종 화면. 2026-10-31, 한국 시간.
배포: windows-demo-restart-handoff-fix-20261031 / 2383dd4.
작업 ID: a51c3737-ce9c-4645-ba59-44c259f32e91.

## 실제 출력

- restart_check: OPEN_ORDER_API_RESTART_VERIFIED
- scope: API_RESTART_WITH_INDEPENDENT_GUARD
- api_instance_before: e20a39c1-8e9e-45ad-a44b-f13df5f54804
- api_instance_after: ada1f9be-92a9-4fd1-836a-19ebec7bf636
- same_order_observed_before_after: true
- quantity: 0.0007 / limit_price: 77070.10
- new_entry_post_count: 1 / entry_post_attempts: 1
- recovered_existing_intent: false
- failure / failure_stage / completion_failure: NONE
- ready_at_utc: 2026-10-31T08:11:18.768534+00:00 (한국 시간 17:11:18)
- wait_deadline_utc: 2026-10-31T08:12:03.768534+00:00
- wait_elapsed_ms: 13740
- api_reads: 4 / api_unavailable_count: 1
- risk_state: NORMAL
- flat_confirmation: VERIFIED_FLAT
- history_and_baselines_preserved: true
- filled_quantity: 0.0000
- final_paused: true
- orders_enabled / live_trading_enabled: false

B창은 restart_handoff READY, guard_fence 46, database_changed false를 출력한 뒤 API를 재시작했다. 이후 DB/API 모두 Healthy였다.

## 판정과 범위

DEMO 미체결 주문이 있는 동안 API 컨테이너를 재시작하고, 별도 실행 컨테이너가 생존하여 변경된 API 인스턴스와 동일 미체결 주문을 확인했다. 신규 진입 제출은 총 1회이며 중복 제출이 없었다. 최종 정리 후 flat 확인, 일시정지, NORMAL 및 기존 이력·기준값 보존을 확인했다.

이번 결과는 API 재시작 범위의 통과다. 독립 guard 자체의 강제 종료, 호스트/DB 재시작, 보유 포지션 중 재시작, 부분체결 보호 성공 또는 장시간 운용을 검증한 것은 아니다. Phase4 전체 완료 또는 LIVE 승인으로 확대하지 않는다.

이전 eac8d62b-c2e2-4ebd-8be0-f6a408dfb882의 NOT_ACCEPTED 결과는 그대로 보존한다. 직전 09f48828-3294-4234-80f6-0fd79a5e7565 실행은 recovered_existing_intent true / RECOVERY_ONLY / 신규 제출 0 / VERIFIED_FLAT였으며, 재시작 통과 증거로 사용하지 않는다. 해당 기존 의도가 처음 만들어진 시점과 경위는 제공 화면만으로 확정하지 않는다.
