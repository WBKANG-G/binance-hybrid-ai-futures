# 실제 빈 계정 소켓 단절·재연결 통과 — 2026-10-31 KST

사용자의 터미널 캡처에 근거합니다. 배포판 windows-demo-disconnect-check-20261031, commit b0adbc5, ZIP SHA256 db0f1ce1a13bc40b12cee68e59e46301b038e8ddf82bc49a04b5d65db806fe0e.

설치 컨테이너 Healthy와 숨김 메모리 키 등록 REGISTERED_IN_PRIVATE_TMPFS를 확인했습니다. scripts.check_demo_disconnect --enable-fault-injection 실제 실행은 READY → BLOCKED → RECONNECTED를 출력한 뒤 정상 종료했습니다.

- disconnect_check EMPTY_SOCKET_LOSS_RECONNECT_VERIFIED.
- scope EMPTY_ACCOUNT_LOCAL_SOCKET_CLOSE.
- injected_close_count1, reconnect_count1, disconnect_code SOCKET_CLOSED.
- entry_gate_blocked_after_disconnect true, reconnect_reconciled true.
- history_and_baselines_preserved true.
- final_paused true, final_stream_ready false, final_reconciled false, risk_state NORMAL.
- entry_signal_supplied false, orders_enabled false, live_trading_enabled false.

이 검사는 진입 신호 없이 빈 계정에서 로컬 소켓을 한 번 의도적으로 닫아 기존 수신기/게이트 차단을 확인하고 한 번 재연결해 REST 재대조한 실제 DEMO 증거입니다. 최종 stream_ready/reconciled false는 검증 후 세션을 정상 종료한 결과이며 재연결 실패를 의미하지 않습니다. 당시 재연결/재대조 true와 정상 종료 상태를 구분합니다. 과거 주문/미해결 이력/기간 기준을 리셋하지 않았습니다.

독립된 인터넷 전체 장애, 열린 진입 주문 또는 보호 포지션 중 WS 단절/서버 재시작, 실제 부분 체결 보호 수락, 무인 장시간 운영, 원본 WS 장기 보관과 전체 Phase4 완료를 검증한 것으로 확대하지 않습니다. 현재 정상 연결/기본 전체 체결·보호·청산, 명시적 복구 확인, 빈 API 재시작/REST 재대조, 빈 계정 소켓 단절/재연결 증거가 확보돼 있습니다. LIVE/Phase5 승인은 없습니다.
