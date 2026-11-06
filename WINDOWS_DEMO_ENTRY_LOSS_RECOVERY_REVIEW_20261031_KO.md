# 응답 유실 시험 후 전체 복구 검토 통과

사용자 CMD 캡처. windows-demo-entry-response-loss 배포 / 0e03d75.
대상 시험 9be80b67-25d4-43d6-b18b-7d12fdd9cd0e.

- recovery REVIEW_ONLY_NO_DATABASE_CHANGE
- review_token 4806a8983d682f20c5fc0bdf251d494f2cbe91f3f58ec1a04de74c51ef48388c
- audited_request_count60 / audit_batch_count8
- signed_query_count264 / audit_clock_probes0
- unresolved_count3 / risk_gates PASS
- position_nonzero false / active_orders0
- audit_transport_mode POOLED_HTTPS
- audit_http_attempts264 / audit_connection_attempts1 / audit_reused_requests263
- audit_server_close_responses0 / audit_transport_elapsed_ms15904
- audit_last_route OTHER_GET
- risk_state EMERGENCY / paused true
- orders_enabled false / live_trading_enabled false

현재 전체 복구 감사가 완료됐다. 기존 포지션 조회 없음 및 로컬 CLOSED/exit FILLED 증거에 더해 현재 활성 주문0·리스크 통과를 확인했다. 전송 측정15.904초로 기존40초 상한 안에서 완료했다. 아직 잠금 해제는 적용하지 않았다. 미해결 이력3건의 신원을 집계 출력으로 확정하거나 지우지 않는다. 원래 시험 NOT_ACCEPTED와 별도 confirm-flat 일반 오류 기록을 성공으로 덮어쓰지 않는다.

다음은 최신 상태를 다시 검토하고 토큰이 같을 때만 NORMAL/paused로 전환하는 기존 명시적 승인 명령이다. 신규 주문/청산/보호 POST는 없다. 적용 작업 ID 
25cd5a54-fe2c-451f-b0c1-b738814ea522
