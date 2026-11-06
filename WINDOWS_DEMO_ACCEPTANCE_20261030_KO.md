# Windows 실제 DEMO 검증 기록 — 2026-10-30

사용자가 공유한 최종 터미널 캡처에서 다음 한 건의 실제 거래소 DEMO 기본 흐름 통과를 확인했습니다. 이 기록은 Phase4 전체 수락이나 LIVE/Phase5 승인이 아닙니다.

- 배포판: windows-demo-event-clock-fix-20261030, commit fed3f9a0123b8803fabae0886fe3678bdeddae1b
- ZIP SHA-256: 36f3e3d09db96e7341200ffd58f5f4f645025c260abf7187f958577bd0034d01
- 계정: DEMO-WINDOWS-ISOLATED-BTC-V1, ISOLATED BTCUSDT2배
- 작업: 2d454f68-622d-49f6-9a7e-969b71f0e247
- 방식: DEMO_VALIDATION/LONG/OPPOSITE_QUOTE/LIMIT
- 수량/수량cap: 0.0007 BTC
- 제시된 진입 지정가: 77395.70 USDT(실제 평균 체결가로 해석하지 않음)
- 손절/익절 제시가: 76925.00 / 78169.70 USDT
- 결과: FINISHED_FLAT, entry_filled=true, FULL_FILL, entry_state=FILLED, position_state=CLOSED
- stop_verified=true, target_verified=true, filled_protection_and_flat_verified=true
- session_error=false, session_failure=NONE, completion_failure=NONE, completion_database_failure=NONE
- 수신6개 / 합친 유효 알림5개
- 진입 준비 콜백20174ms, 검증된 시각 중간 보정값102ms / RTT146ms / 추가 시각 대기 요청0ms
- 이벤트 진단 code=NONE, 종료 시 stage=RECEIVE, 현재 event_kind=NONE / event_age_ms=null
- 마지막 event_local_age_ms21393은 종료 시 현재 이벤트 나이로 단정하지 않음(마지막 진단 표본일 수 있음)
- 마지막 스냅샷 불일치필드[], 초기 체결 추가 경로 미사용(initial_fill_settled=false)
- live_trading_enabled=false

SL·TP는 거래소에서 접수·조회 확인된 것이며 실제 손절/익절 트리거 체결을 이 캡처로 확인한 것은 아닙니다. 검증 종료의 reduce-only 청산/최종0포지션 확인을 통과한 한 건입니다. 실제 부분 체결·연결 끊김·실행 중 프로세스 재시작·무인 장시간 운영·원본 WS 이력 장기 보관 등의 전체 수락은 별도입니다. 클라우드 합성895개 검사 통과를 실제 수락 개수와 혼동하지 않습니다.

종료 코드는 완료 후 PAUSE/EMERGENCY_FLATTEN 경로를 거치므로 일시 정지·비상 상태를 유지하는 설계입니다. 캡처는 risk_state/paused를 직접 출력하지 않았으므로 별도 로컬 조회로 현 상태를 확인해야 합니다. 다음 새 진입을 자동 시작하거나 이 완료 작업을 임의로 반복하지 않습니다.

다음 준비 확인: recovery.review는 누적 요청30개를 넘으면 REQUEST_AUDIT_LIMIT로 중단합니다. 기존 주문과 미해결2건을 지우지 않고 로컬 누적 요청 수를 확인한 뒤 다음 장애/복구 검증 경로를 결정합니다. 요청 수는 아직 이 결과 캡처만으로 확정하지 않았습니다.

## 후속 로컬 조회 확인

추가 사용자 캡처: POST_VALIDATION_READ_ONLY에서 risk_state=EMERGENCY, paused=true, tracked_requests=31, local_positions={}, order_state=FILLED, position_state=CLOSED, open_quantity=0, exit_state=FILLED를 확인했습니다. 기본 흐름 통과 후 안전한 정지 상태가 확인됐습니다. 현재30개 복구 감사 한도를 넘으므로 기록 삭제 없이 자원 제한이 있는 전체 요청 검토를 보완 필요를 확인했습니다.
