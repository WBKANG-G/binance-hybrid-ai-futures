# 실제 미체결 주문 중 WSS 단절 검증 미통과

사용자 CMD 화면 근거, 2026-10-31 한국 시간.
ZIP windows-demo-open-disconnect-check, SHA256 86e938597771647f078701f24c41b2f74c2d19e5896c78204a93cf4d554e7530.
작업 4e81bbe7-12f8-4e65-848a-f3a928b13a6e.

READY 출력 후 NOT_ACCEPTED.
- injected_close_count 1 / reconnect_count 0
- disconnect_code NOT_VERIFIED / entry_gate_blocked_after_disconnect false
- same_order_observed_before_after false / cancelled_unfilled_order_verified false
- 신규 진입 POST1 / attempts1 / recovered_existing_intent false
- failure OTHER_VALIDATION_FAILURE / failure_stage VERIFY_OPEN_ORDER
- completion_failure NONE / flat_confirmation VERIFIED_FLAT
- history_and_baselines_preserved true / filled_quantity 0.0000
- risk_state NORMAL / final_paused true / stream_ready false / reconciled false
- orders_enabled false / LIVE false

검증 미통과 상태로 보존한다. close 요청 1회가 단절 확인 성공을 뜻하지 않는다. 재연결은 수행되지 않았다. stage가 READY 이후에도 VERIFY_OPEN_ORDER에 남고 TimeoutError가 공통 OTHER_VALIDATION_FAILURE로 분류될 수 있어 실제 원인을 이 출력만으로 확정하지 않는다. 최초 세션의 wait_for45초가 초기 연결·준비·단절·session finally 정리를 모두 포함하므로 정리 소요에 따른 타이머 가능성을 조사하되 사실로 단정하지 않는다.

최종 flat/NORMAL/paused 확인으로 재실행이나 비상 해제는 필요하지 않다. 현재 fence의 허용된 감사 시각/개인 세션 고정 코드/주문 상태만 DB 강제 READ ONLY로 조회하는 후속 명령을 준비했다. API 키 접근, 거래소 호출, lease획득, DB쓰기는 없다.

## 추가로 받은 강제 READ ONLY 조회 결과

현재 주문 CANCELED/CLOSED, filled 0.0000, remaining 0, open_quantity 0, risk NORMAL, paused true.
last_private_session_failure: stage RECEIVE, code SOCKET_CLOSED, event_kind NONE, ready_callback_ms 19166.

감사 UTC (한국 시간은 +09:00):
- 2026-10-31T08:46:56.835974+00:00 DEMO_OPEN_WS_FAULT_READY
- 2026-10-31T08:47:03.381453+00:00 DEMO_PRIVATE_SESSION_FAILED
- 2026-10-31T08:47:03.410348+00:00 DEMO_STREAM_UNTRUSTED
- 2026-10-31T08:47:03.604852+00:00 DEMO_OPERATION_INTENT
- 2026-10-31T08:47:17.216203+00:00 DEMO_OPERATION_INTENT
- 2026-10-31T08:47:44.066213+00:00 DEMO_FLAT_CONFIRMATION_CHECK
- 2026-10-31T08:48:14.431387+00:00 DEMO_STREAM_UNTRUSTED
- 2026-10-31T08:48:14.469915+00:00 DEMO_OPEN_WS_FAULT_RESULT
- 2026-10-31T08:48:14.529519+00:00 DEMO_LEADER_RELEASED

같은 fence의 지정된 이벤트 조회에는 DEMO_SESSION_CLOSE가 없었다. 출력은 최대25건 조회 중9건이었다. 원본 진입/준비 시작 시각과 원래 예외 객체는 출력에 없으므로 타이머의 정확한 시작/예외 종류를 이 기록만으로 단정하지 않는다. 다만 원래 session 전체를 wait_for45초로 감싸면 SOCKET_CLOSED 기록 후 finally 정리 중 타이머가 취소를 주입할 수 있는 코드 경로가 확인됐으며, 단절을 제시간에 관측했어도 정리를 지연시키면 기존판이 거부하는 재현 테스트를 확보했다.

이 실제 시도는 재연결0의 미통과로 유지한다. SDK/위험 제한을 완화하지 않고 단절 관측 타이머와 session 정리 완료 대기를 분리하는 수정판은 별도 새 UUID의 실제 검증이 필요하다.
