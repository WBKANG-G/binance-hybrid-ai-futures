# 취소·미체결 주문의 잘못된 로컬 포지션 기록 정정

기존 DB가 있는 Galaxybook PC에서 실행합니다. 이번 설치에는 미리보기와 적용 기능이 함께 들어 있습니다. 우선 미리보기 결과를 공유하고, 그 토큰으로 적용할 때는 다시 설치할 필요가 없습니다.

1. ZIP을 모두 추출하고 windows-demo-cancelled-correction 폴더에서 infra가 보이는 위치로 들어갑니다.
2. Docker Desktop이 켜진 상태에서 탐색기 주소창에 cmd를 입력합니다.
3. 실행합니다.

    docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait

4. 모두 Healthy면 변경 전 미리보기를 실행합니다.

    docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.review_demo_cancelled_correction

5. DEMO API Key/Secret을 숨김 입력하고 마지막 결과를 대화에 공유합니다. 키는 대화에 보내지 마세요. 기본 실행은 DB 읽기와 거래소 GET만 수행하며 lease 획득/DB 변경/주문 실행이 없습니다.

성공 출력은 correction=REVIEW_ONLY_NO_DATABASE_CHANGE입니다. proposed_orders에 변경될 주문과 세 필드가 표시됩니다.
- open_quantity: 잘못 배정된 수량 → 0
- actual_risk: 잘못 배정된 포지션의 위험 금액 → 0
- position_state: EXIT_PENDING → CLOSED (해당 주문이 소유한 포지션이 없다는 뜻)

entry_state=CANCELED, filled=0, remaining=0, exit_state=UNKNOWN 및 exit_id, 실제 체결한 다른 주문, 과거 UNKNOWN/SUBMITTING 요청, 정책/계정 평가액/일·주 기준/고점/입출금/관측 시각은 유지합니다. EMERGENCY/paused도 유지합니다. CLOSED를 실제 종료 체결의 증거로 사용하지 않습니다.

review_token은 비밀키가 아니라 검토 대상 계정·원본 기록·거래소 스냅샷·체결/income 근거·KST 일/주를 묶은 확인값입니다. 적용하려면 이 토큰과 고정 UUID 작업 ID를 명시해야 하며, 적용 시 다시 GET 검증합니다. --apply-correction TOKEN --operation-id UUID 두 옵션을 모두 지정하지 않으면 적용하지 않습니다. 이 문서에는 자동 적용 명령이나 사전 승인된 토큰이 없습니다.

적용은 계정 lease/fence와 행 잠금 아래에서 진행하며, 정정 전 주문 전체와 근거 해시/시각/토큰을 감사 기록에 함께 저장합니다. 처리 중 잠금이 만료되거나 근거가 바뀌면 정정 트랜잭션은 롤백됩니다. 잠금 획득/해제의 별도 감사 이벤트는 남을 수 있습니다. 같은 작업 ID와 토큰을 다시 주면 ALREADY_RECORDED_NO_ACTION으로 중복 정정하지 않습니다. 오류 시 임의 재실행·새 작업 ID 발급을 하지 말고 코드만 공유하세요.

적용 후에도 recovery_eligible=false, orders_enabled=false, live_trading_enabled=false이며 실거래와 Phase 5를 시작하지 않습니다. 자정 평가액 문제와 미확정 요청에 대한 별도 검토는 남아 있습니다. DB 초기화, 새 ledger, volume 삭제, down -v, 예전 주문 명령을 실행하지 마세요. 적용을 시도한 뒤에는 감사 필드가 추가되므로 이 버전 또는 이를 포함한 이후 버전을 계속 사용하세요.
