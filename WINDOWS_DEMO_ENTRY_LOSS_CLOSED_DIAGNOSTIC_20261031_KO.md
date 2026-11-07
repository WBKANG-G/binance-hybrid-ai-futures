# Windows DEMO 응답 유실 시험 후 현재 포지션·장부 진단

근거: 사용자 CMD 캡처. windows-demo-entry-response-loss 배포.
대상 작업 ID 9be80b67-25d4-43d6-b18b-7d12fdd9cd0e.

## 원격 signed GET

SIGNED_POSITION_GET_ONLY / positions []

단일 BTCUSDT 필터 조회에서 현재 BTCUSDT 포지션 행이 반환되지 않았다. 이 결과를 모든 주문/보호 정리 확인으로 확대하지 않는다.

## 로컬 읽기 전용 진단

LOCAL_DATABASE_READ_ONLY / risk_state EMERGENCY / paused true / failure_category OTHER_DEMO_FAILURE
order state FILLED / position_state CLOSED / filled0.0007 / open_quantity0 / protected false / exit_state FILLED
live_trading_enabled false

종료 의도가 ACKNOWLEDGED에서 FILLED로 장부에 반영돼 현재 관리 노출0 및 CLOSED가 확인됐다. 원격 포지션 조회와 결합해 종료 체결을 확인하되 잔여 SL/TP·전체 이력·현재 리스크 검토는 별도 필요하다. 전체 시험 결과 NOT_ACCEPTED를 성공으로 덮어쓰지 않는다.

## 최근 이벤트 UTC / KST

- 2026-10-31 13:55:45.523400+00:00 DEMO_REDUCE_ONLY_INTENT (KST22:55:45)
- 13:55:45.572115 DEMO_REDUCE_ONLY_EFFECT
- 13:55:46.078042 HEARTBEAT
- 13:56:00.410806 DEMO_ENTRY_LOSS_CHECK_RESULT
- 13:56:00.466288 DEMO_LEADER_RELEASED
- 13:58:02.837382 DEMO_LEADER_ACQUIRED
- 13:58:03.511560 DEMO_RECONCILING
- 13:58:03.546069 DEMO_REST_SNAPSHOT
- 13:58:09.097727 DEMO_READ_ONLY_OBSERVATION
- 13:58:09.120832 HEARTBEAT
- 13:58:09.352979 DEMO_FLAT_CONFIRMATION_QUERY
- 13:59:09.581231 DEMO_LEADER_RELEASED (KST22:59:09)

전체 최근 이벤트의 failure_category OTHER_DEMO_FAILURE는 저장된 장부 필드다. 각 이벤트마다 새 오류가 발생했다고 단정하지 않는다. confirm-flat의 QUERY 시작부터 리더 반환까지 약60.228초이며, 원래 confirm_flat은 전체 과거 이력을 포함한 복수 스냅샷을 하나의 fenced 효과 안에서 조회한다. SDK 시계 수명과 lease 모두60초이지만 이 집계 출력만으로 정확한 예외/최종 원인을 확정하지 않는다. 상한 연장·조회 생략·lease 변경을 하지 않는다.

다음 단계는 이미 배포된 HTTPS 연결 재사용이 적용되는 인자 없는 review_demo_recovery 읽기 전용 검토다. 잠금/장부를 바꾸지 않고 전체 요청·독립 스냅샷·활성 일반/Algo 주문·현재 리스크를 검사한다. BLOCKED면 반복하지 않는다. 통과해도 별도 승인 전까지 EMERGENCY/paused 유지. 신규 진입/청산/보호 POST를 하지 않는다.
