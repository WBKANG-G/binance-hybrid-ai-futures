# Windows DEMO 응답 유실 시험 후 비상 상태 복구 승인 완료

근거: 사용자 CMD 캡처. windows-demo-entry-response-loss / 0e03d75.
적용 작업 ID: 25cd5a54-fe2c-451f-b0c1-b738814ea522.
검토 토큰: 4806a8983d682f20c5fc0bdf251d494f2cbe91f3f58ec1a04de74c51ef48388c.

실제 결과:
- recovery EMERGENCY_ACKNOWLEDGED_ENTRIES_STILL_PAUSED
- orders_enabled false
- live_trading_enabled false

최신 전체 검토 및 토큰 일치 확인 뒤 원래 승인 함수가 risk_state NORMAL로 전환하고 paused true / stream_ready false / reconciled false를 유지했다. 미해결 이력3건과 기존 요청/주문/UNKNOWN 증거/기준금/과거 이벤트를 보존한다. 주문 실행 및 LIVE 거래는 비활성이다.

직전 검토는 요청60개/8배치/서명GET264회/추가 시계0회/미해결3건/리스크PASS/포지션없음/활성주문0이었다. HTTPS 연결1개/재사용263회/서버종료0/전송측정15904ms로 완료됐다.

앞선 작업9be80b67-25d4-43d6-b18b-7d12fdd9cd0e는 로컬 ACK 유실1회, UNKNOWN 기록·진입 차단·동일 ID signed GET 복구·신규 진입1건을 확인했으나 자동 정리 관찰에서 FLAT_NOT_CONFIRMED 및 completion_failure DEMO_SESSION_OR_QUERY_FAILED로 NOT_ACCEPTED였다. 이후 기존 종료 FILLED/로컬CLOSED/노출0과 현재 원격 포지션 없음, 전체 검토 활성주문0을 확인하고 별도 복구 승인을 적용했다. 앞선 NOT_ACCEPTED 결과를 통과로 덮어쓰지 않는다.

confirm-flat 일반 오류 및 약60초 관찰 구간의 정확한 원인은 아직 확정되지 않았다. 동일 명령 재실행 또는 신규 진입은 필요하지 않다. 다음 개발은 추가 거래보다 기존 종료 확인 경로의 지연/오류 단계를 재현·진단하는 것이 우선이다. SL 실패 실제 DEMO 검증 미완료, 실제 부분체결 보호 미완료를 유지하며 Phase4 전체 완료/LIVE/Phase5 전환으로 확대하지 않는다.
