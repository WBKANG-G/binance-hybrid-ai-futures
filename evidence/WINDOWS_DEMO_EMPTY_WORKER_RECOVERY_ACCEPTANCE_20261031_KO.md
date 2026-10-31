# Windows DEMO 빈 계좌 관찰 프로세스 SIGKILL·리더 복구 실제 통과

근거: 사용자가 제공한 CMD 최종 화면. 2026-10-31 한국 시간.
배포: windows-demo-empty-worker-recovery-20261031 / d236930.
ZIP SHA256: aff2c645855ab0b2ef127a3c00194b1d811696af95fce3671262db7ac5acdc96.
작업 ID: b4cceb33-35bd-4430-b5be-65a49924d9ba.

## 실제 결과

- 빌드 후 web/db/api Healthy, private tmpfs 키 등록 완료
- 단계 WORKER_STARTED → WORKER_READY_KILLING → WORKER_SIGKILL_VERIFIED → WAIT_NATURAL_LEASE_EXPIRY → NEW_LEADER_RECONCILED
- worker_recovery EMPTY_WORKER_SIGKILL_FENCED_RECOVERY_VERIFIED
- scope EMPTY_ACCOUNT_OBSERVATION_WORKER_SIGKILL
- worker_pid 46 / worker_exit_code -9 / forced_kill_verified true
- competing_leader_blocked true
- lease_fence_before 56 / lease_fence_after 57
- natural_lease_wait_ms 59699
- stale_owner_effect_blocked true / stale_owner_renew_blocked true
- entry_gate_blocked_before_reconciliation true
- new_leader_reconciled true
- history_and_baselines_preserved true
- failure NONE / failure_stage NONE / cleanup_failure NONE
- risk_state NORMAL / final_paused true
- final_stream_ready false / final_reconciled false
- leader_released true
- new_entry_post_count 0 / entry_signal_supplied false
- orders_enabled false / live_trading_enabled false

## 판정과 범위

실제 Windows DEMO 환경에서 API 컨테이너 안의 별도 관찰 프로세스를 강제 종료하고, 기존 리더 권한의 자연 만료를 약59.7초 기다린 뒤 새 리더가 정확히1 큰 fence를 획득했다. 재대조 전 신규 진입 게이트 차단, 이전 소유자의 효과 함수 및 갱신 거부, REST/WSS 재대조, 새 세션 정상 종료, 최종 리더 권한 해제, 이력·기준 보존을 확인했다.

이번 통과 범위는 빈 계좌의 관찰 프로세스 중단·복구다. 주문/포지션을 보유한 주문 Worker 중단, API/DB/호스트 전체 중단, 실제 부분체결 보호, 장기 무인 운용 또는 Phase4 전체 통과를 의미하지 않는다. 추가 정리, 비상 잠금 해제, 같은 작업 재실행은 필요하지 않다.
