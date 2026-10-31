# 손절 로컬 실패 대응 후 비상 상태 복구 승인 완료

근거: 사용자 CMD 캡처. 배포 windows-demo-flat-confirmation-fix-20261031 / 6e88c76.
적용 작업 ID: 6c1c7945-79bb-4d3a-b9b2-44c761863c3a.
검토 토큰: 9767ec69e532eb3140a354a481ba30aa0a805e30465274a423950aee5c780afa.

실제 출력:
- recovery EMERGENCY_ACKNOWLEDGED_ENTRIES_STILL_PAUSED
- orders_enabled false
- live_trading_enabled false

기존 승인 함수가 최신 전체 검토와 토큰 일치를 다시 확인한 뒤 risk_state NORMAL로 전환했다. paused true, stream_ready false, reconciled false를 유지한다. 미해결 이력4건과 기존 요청/주문/전송하지 않은 증거/회계/기준금/과거 이벤트를 보존한다. 적용 결과에 현재 잔고 수치는 없으므로 잔고를 추정하지 않는다.

직전 읽기 전용 검토는 요청63개/8배치/서명GET276회/추가 시계0회/미해결4건/리스크PASS/포지션없음/활성주문0이었다. HTTPS 연결1개/재사용275회/서버종료0/전송측정15623ms로 완료됐다.

손절 로컬 조립 실패 시험77db044e-1bcd-4358-80e6-df9730630213는 실패 주입1회, 원래 진입 차단/자동 비상 종료, 진입1건/종료1건, 보호 전송 시도0, 후속 강제 정리 없음, FILLED/VERIFIED_FLAT/이력·기준금 보존/실패NONE으로 통과했다. 이후 최신 복구 검토 및 명시적 비상 상태 승인을 별도 완료했다. 같은 명령 또는 장애 시험을 반복할 필요가 없다.

통과 범위는 로컬 SL 조립 실패 경로이며 실제 거래소 SL 거부·외부 긴급 알림 전달까지 포함하지 않는다. 실제 부분체결 보호와 주문/포지션 worker 강제 종료 등 미완료 항목 및 앞선 응답 유실 시험 NOT_ACCEPTED 기록을 유지한다. 신규 진입 재개·Phase4 전체 완료·LIVE·Phase5 전환으로 확대하지 않는다.
