# Windows DEMO SHORT 검증 후 복구 완료

사용자 CMD 화면 근거, 2026-10-31 한국 시간.

SHORT 작업 095b9d5c-b79d-4731-9d64-971e4ffde7d0 전량 체결·보호·flat 통과 후 preflight에서 EMERGENCY가 확인됐다. 검증 코드는 잔여 포지션 정리에 EMERGENCY_FLATTEN을 사용하고 이 명령은 잠금을 유지한다. 실제 해당 잠금의 모든 전이 원인을 개별 감사 이벤트로 확정했다는 뜻은 아니다.

복구 조회 REVIEW_ONLY_NO_DATABASE_CHANGE:
- audited_request_count 48 / audit_batch_count 6
- signed_query_count 216 / audit_clock_probes 1
- unresolved_count 2 / risk_gates PASS
- position_nonzero false / active_orders 0
- risk_state EMERGENCY / paused true

이 조회의 토큰으로 작업 88bc4aa7-8fc4-4940-8c2c-a759a05d1022 적용 결과 EMERGENCY_ACKNOWLEDGED_ENTRIES_STILL_PAUSED를 실제 확인했다. 거래소 주문 제출 없이 NORMAL 복구, 진입 paused 유지, 기존 미해결 요청 2건 보존. 새 검증 전의 마지막 확인 상태다. 과거 토큰·작업을 다시 적용하지 않는다.
