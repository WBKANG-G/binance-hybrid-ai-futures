# 복구 사전 검토 검증 범위

services/execution/demo_recovery.py의 기본 review는 기존 DB를 읽고 세 번의 거래소 snapshot 및 원래 요청 ID의 GET 조회로 현재 flat/주문 없음/설정/손실 게이트/상태 불변을 확인합니다. NOT_FOUND는 미확정으로 보존합니다. 조회 실패, 원래 ID와 다른 응답, 활성 또는 해석 불가능한 주문은 거부합니다. 검토 토큰은 원래 ledger(lease가 초기화하는 관측 준비 플래그 제외), 거래소 fingerprint, 개별 요청 관측 결과를 함께 묶습니다.

acknowledge_flat_emergency는 자동 호출되지 않습니다. 읽기 전용 DEMO 클라이언트, 전용 격리 BTCUSDT 설정, 기존 paused EMERGENCY, 로컬 잔량/보유량 0, 현물/교차가 아닌 정확한 Isolated 2x, 관측 간 상태 불변 및 손실/날짜 경계 게이트 통과를 요구합니다. 미확정 이력을 인정한다는 명시적 플래그와 일치하는 검토 토큰이 있어야 기존 fence/lease 검증과 계정 row lock 아래에서 감사 기록을 남깁니다. 토큰 불일치나 lease 만료면 변경은 롤백됩니다.

이 기능은 과거 요청이 전송되지 않았다는 증명이 아닙니다. 알려진 flat 상태와 보존된 미확정 이력에 대한 운영자 확인을 기록하는 기능입니다. 실제 적용은 risk_state NORMAL로 전환하되 paused=true 및 reconciled/stream_ready=false를 유지하므로 적용 자체로 진입을 허용하거나 주문을 보내지 않습니다. 기존 resume/신호/위험 게이트를 우회하지 않습니다. 기존 DAILY/WEEKLY/DRAWDOWN 잠금을 해제할 수 없습니다.

새 통합 테스트는 기본 검토의 DB 불변/GET-only, 명시적 적용 감사 기록과 과거 상태/손실 기준 보존, 적용 후 계속 일시정지, 중복 작업 무동작, 잘못된 토큰/미승인/손실/날짜 경계/잔량/손실 잠금/조회 실패/응답 ID 오류/활성 주문/lease 만료 차단, CLI 기본 검토의 lease 미획득, 불완전 적용 인수의 키 입력 전 차단을 확인합니다. 실제 PostgreSQL, 합성 거래소 응답만 사용했습니다. 실제 Binance 인증·주문 호출 및 Windows 잠금 변경은 수행하지 않았습니다.

과거 두 미확정 요청은 현재 상태 그대로입니다. Windows에서는 기본 review만 실행하고 실제 적용 명령은 실행하지 않습니다. Phase 4 미완료, 실거래 및 Phase 5 금지.
