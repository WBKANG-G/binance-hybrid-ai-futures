# 손절 로컬 실패 시험 후 복구 검토 통과

근거: 사용자 CMD 캡처. 배포 windows-demo-flat-confirmation-fix-20261031 / 6e88c76.
대상 SL 시험77db044e-1bcd-4358-80e6-df9730630213.

- recovery REVIEW_ONLY_NO_DATABASE_CHANGE
- review_token 9767ec69e532eb3140a354a481ba30aa0a805e30465274a423950aee5c780afa
- audited_request_count63 / audit_batch_count8
- signed_query_count276 / audit_clock_probes0
- unresolved_count4 / risk_gates PASS
- position_nonzero false / active_orders0
- audit_transport_mode POOLED_HTTPS
- audit_http_attempts276 / audit_connection_attempts1 / audit_reused_requests275
- audit_server_close_responses0 / audit_transport_elapsed_ms15623
- audit_last_route OTHER_GET
- risk_state EMERGENCY / paused true
- orders_enabled false / live_trading_enabled false

최신 전체 요청 감사·원격 스냅샷·기간/리스크 검사를 통과했다. 미해결 집계는 이전3건에서4건이 됐지만 이 출력만으로 새1건의 세부 신원을 확정하지 않는다. 로컬 SL NOT_SENT 증거와 과거 모호한 요청을 삭제/재분류하지 않는다. 전송 측정15.623초로 기존40초 예산 안에서 완료했다. 아직 복구 승인은 적용하지 않았다.

다음 적용 작업 ID: 
6c1c7945-79bb-4d3a-b9b2-44c761863c3a
최신 검토를 다시 수행하고 토큰 일치 시에만 기존 승인 함수가 NORMAL/paused로 전환한다. 미해결4건·주문/회계/기준금/과거 증거를 보존한다. 새 진입/종료 POST나 자동 재개를 하지 않는다. SL 시험의 실제 로컬 실패 대응 통과와 별도 복구 승인을 구분한다.
