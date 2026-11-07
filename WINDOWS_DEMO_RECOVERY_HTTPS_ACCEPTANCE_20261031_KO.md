# Windows DEMO 복구 HTTPS 연결 재사용 검토 통과

사용자가 제공한 CMD 캡처 기준. 배포 windows-demo-recovery-https-reuse-20261031 / 4bd1ca2.
ZIP SHA256 628b01a3e239812a20553ea8ee411be3384059f737e77d204ee30b79eca72f65.

- 빌드 후 DB/web/API Healthy, 키 RAM 등록 성공
- recovery REVIEW_ONLY_NO_DATABASE_CHANGE
- review_token 136f461a61ecd53d2a027aa6cb11d7a8e1015a20714e9e46dc21b78260b11529
- audited_request_count 55 / audit_batch_count 7
- signed_query_count 244 / audit_clock_probes 0
- unresolved_count 3 / risk_gates PASS
- position_nonzero false / active_orders 0
- audit_transport_mode POOLED_HTTPS
- audit_http_attempts 244 / audit_connection_attempts 1
- audit_reused_requests 243 / audit_server_close_responses 0
- audit_transport_elapsed_ms 13891 / audit_last_route OTHER_GET
- risk_state EMERGENCY / paused true
- orders_enabled false / live_trading_enabled false

이전 REQUEST_AUDIT_TIME_BUDGET 뒤 수정본의 실제 Windows 읽기 전용 검토가 전체55개 이력 조회를 완료했다. 연결1개를243회 재사용했고 전송 측정13.891초로 기존40초 제한 안에서 완료했다. 이는 명령 전체 소요 시간과 같다고 단정하지 않는다. 시계0회는 이 검토 감사 안의 추가 프로브 수이며 명령 시작 시 SDK 시계 동기화 생략을 뜻하지 않는다.

미해결3건의 세부 신원은 이 집계 출력만으로 확정하지 않는다. 미해결 이력을 지우거나 상태를 재분류하지 않는다. 현재 포지션/활성 주문0 및 리스크 통과를 확인했지만 아직 복구 승인은 적용하지 않았다. 다음 별도 승인 명령은 최신 상태를 다시 감사해 토큰이 같을 때만 EMERGENCY를 NORMAL로 바꾸고 entries paused는 유지하며, 과거 이력과 기준금은 보존한다. 새 주문/Phase5/LIVE 승인이 아니다.
