# 취소 주문의 과거 수량 오배정 — 읽기 전용 이력 검토

기존 DB가 있는 WBKANG_Galaxybook PC에서 실행하세요. 새 PC의 빈 DB에는 기존 거래 이력이 없습니다.

이번 수정은 비상 조회 시 체결량 0인 종료 주문에 다른 거래의 포지션을 배정하지 않도록 합니다. 귀속을 알 수 없는 실제 포지션도 EMERGENCY/paused 상태를 유지합니다. 이미 잘못 저장된 기록을 자동 수정하거나 UNKNOWN을 성공/실패로 바꾸지 않습니다.

1. ZIP을 모두 추출하고 windows-demo-cancelled-review 폴더의 infra가 보이는 위치에서 탐색기 주소창에 cmd를 입력합니다.
2. Docker Desktop이 켜진 상태에서 실행합니다.

    docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait

3. 모두 Healthy면 아래 명령을 실행합니다.

    docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.review_demo_cancelled_history

4. DEMO API Key와 DEMO Secret을 숨김 입력하고 마지막 결과를 대화에 보내주세요. 키를 대화에 보내지 마세요. 오류면 반복하지 말고 오류 코드만 공유하세요.

도구는 현재 포지션/활성 주문이 없고 Isolated 2x임을 확인합니다. 로컬의 CANCELED/filled=0/remaining=0/open_quantity=원수량/EXIT_PENDING/exit_state=UNKNOWN 모양만 대상으로, 거래소 원래 clientOrderId와 serverOrderId, 종목/방향/유형/수량/가격, 실제 CANCELED/체결량 0을 반복 조회하여 대조합니다. 거래소에서 찾지 못한 주문은 미체결로 추정하지 않습니다.

확인된 경우 메모리의 분석용 사본에서만 해당 취소 주문의 수량을 제외해 기존 체결/income 이력 조회를 진행합니다. 조회 전후 거래소 스냅샷의 일치와 DB 불변을 확인합니다. 원본 기록, 과거 UNKNOWN/SUBMITTING, 평가액/손실 기준/일자, EMERGENCY/paused는 보존합니다. 분석용 사본은 주문 실행이나 복구 적용에 전달하지 않습니다.

성공 출력은 LEGACY_CANCELLED_HISTORY_EVIDENCE_ONLY입니다. local_ledger_repaired=false, unresolved_requests_acknowledged=false, recovery_eligible=false가 정상입니다. legacy_observation_anchor_utc는 모순이 있는 과거 로컬 관측 시각이며, 정상 flat 기록으로 확정했다는 뜻이 아닙니다. 자정 평가액과 모든 종목의 과거 포지션을 입증하지 않으며, 이번 결과만으로 기준값을 저장하거나 잠금을 해제하지 않습니다.

새 주문, 예전 run_demo 명령, --apply-review, DB 초기화, 새 ledger 생성, down -v를 실행하지 마세요. 기존 hybrid-windows DB 볼륨을 유지합니다. API/web은 PAPER, LIVE_TRADING_ENABLED=false이며 Phase 5를 시작하지 않습니다.
