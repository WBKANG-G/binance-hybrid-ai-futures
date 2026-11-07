# 기존 실제 재시작 미통과의 후속 읽기 전용 증거

사용자 캡처에 근거합니다. 부모 배포판 windows-demo-open-api-restart-20261031, commit d2d645d, ZIP SHA256 78a30b30657c89939d0e6836b653415d8cda39f4e096d21dd7894192f0012a83. 작업 eac8d62b-c2e2-4ebd-8be0-f6a408dfb882입니다.

A는 독립 guard 생성/키 숨김 입력 뒤 OPEN_ORDER_READY_RESTART_API_NOW를 출력했습니다. 수량0.0007, 지정가77001.90, 신규 진입 POST1회입니다. 이전 API 식별자139dcd09-4452-45b7-92ba-69151c9241ab. 최종은 NOT_ACCEPTED, api_instance_after null, same_order_observed_before_after false, entry_post_attempts/new_entry_post_count1, failure NONE, flat_confirmation VERIFIED_FLAT, history_and_baselines_preserved true, filled_quantity0.000, final_paused true, LIVE false였습니다.

B에서는 API restart와 Healthy 복귀를 확인했습니다. 후속 docker inspect 시작 시각은 UTC2026-10-31T05:30:35.44709676Z(한국14:30:35경)이며 현재 API 식별자는 a9b95dfc-c29c-40bc-82e3-924d6501f55b로 바뀌었습니다. 재시작 자체가 발생한 것은 확인됐습니다.

읽기 전용 DB 조회는 risk_state NORMAL/paused true, 현재 주문 CANCELED/CLOSED, filled0.000, remaining0, open_quantity0, exit_state NONE을 확인했습니다. database_changed false, orders_enabled false입니다.

감사 타임라인 UTC(한국 시간은+9시간):

-05:29:43.578642 ENTRY_RESERVED.
-05:29:45.914070 ENTRY_EFFECT.
-05:30:54.826698 SESSION_CLOSE.
-05:31:16.967743 FLAT_CONFIRMATION_CHECK.
-05:31:41.442785 LEADER_RELEASED.

API 시작은 ENTRY_EFFECT 약49.5초 뒤입니다. 그러나 실제 READY 시각·관찰 마감·각 API 조회 결과가 저장되지 않아 45초 관찰 안에 재시작을 관측할 수 있었는지 단정하지 않습니다. 조회한 종류의 해당 작업 이벤트에는 RECONCILIATION_FAILED/PRIVATE_SESSION_FAILED/GATE_LOCKED/RESTART_OBSERVED가 없었습니다. 이것을 보존되지 않은 원본 예외가 없었다는 증거로 확대하지 않습니다. 세션 정리·최종 flat·임대 해제는 API 기동 이후에도 기록됐으므로 독립 감시자는 후속 정리를 수행했습니다.

과거 미통과를 성공으로 변경하지 않습니다. 진단 누락은 별도로 재현한 완료 알림/취소 경합 문제로 보완하고, 다음 실제 시도는 새 UUID와 사전 대기 B 명령으로 수행합니다. 대기시간을 연장하거나 기존 주문/미해결 기록/손실 기준을 리셋하지 않습니다.
