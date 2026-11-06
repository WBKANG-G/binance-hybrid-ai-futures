# Windows 실제 부분 체결 실패 및 후속 안전 상태

사용자 캡처에서 관측한 사실입니다. 전체 Phase4 또는 부분 체결 수락 성공이 아닙니다.

- 설치판 windows-demo-history-span-fix-20261031, 실제 작업0feb6a0e-21b2-4f8b-aad5-f101606dc4bb.
- DEMO_VALIDATION/LONG/PASSIVE/LIMIT, 수량cap0.0007 BTC, 제시 지정가77296.90 USDT, 제시 SL76910.40/TP78071.80.
- 결과 FINISHED_FLAT, entry_filled true, filled_quantity0.0006, entry_state CANCELED, position_state CLOSED, entry_outcome PARTIAL_FILL.
- stop_verified false, target_verified false, filled_protection_and_flat_verified false, session_error true, session_failure DEMO_SESSION_OR_QUERY_FAILED.
- 개인 이벤트 진단 EVENT/SNAPSHOT_CHANGED/ORDER_TRADE_UPDATE, event_age454ms, receive_age143ms, ready_callback17106ms, received12/coalesced3, 시각 보정-737ms/RTT156ms/대기0ms/local_age1113ms.
- completion_stage FLAT_CONFIRMATION, completion_failure NONE, completion_database_failure NONE.
- 마지막 차이 available_balance/equity/isolated_margins/isolated_wallets/margin_used/orders/positions/wallet_balance. settle_reason FILL_PROOF_MISMATCH, initial_fill_settled false. 첫 세 스냅샷 원본과 가장 먼저 차단된 세부 검사 지점은 수집되지 않았습니다.

후속 PARTIAL_FILL_READ_ONLY 조회는 entry_status CANCELED, entry_executed0.0006, entry_average77296.900000, trade_count2를 확인했습니다.

1. 2026-10-31T03:17:49.859Z: BUY0.0003 BTC,77296.90,USDT 수수료0.00463781,실현손익0.
2. 2026-10-31T03:17:52.499Z: BUY0.0003 BTC,77296.90,USDT 수수료0.00463781,실현손익0.

합계0.0006 BTC, 수수료0.00927562 USDT입니다. 거래소 현재 포지션{}, 활성 일반 주문0, 활성 조건부 주문0, risk_state EMERGENCY, paused true, database_changed false, orders_enabled false를 확인했습니다. 기존 단일 실행 증거 경로는 이 두 건 집합을 허용하지 않지만 이번 실패에서 그 조건까지 도달했는지는 확정하지 않습니다. 새 보완판은 실패 이력을 성공으로 덮어쓰지 않습니다.
