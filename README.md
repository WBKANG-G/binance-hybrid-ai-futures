# Phase 4 종료 이후 이력 조회

기존 DB가 있는 이전 PC에서 ZIP을 모두 추출하고 HISTORY_REVIEW_START_HERE_KO.md를 읽으세요. 새 PC의 빈 DB에서는 실행하지 마세요.

조회 명령: `docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.review_demo_flat_history`

BTCUSDT 실제 종료 체결과 그 이후부터 오늘 0시/현재까지의 추가 체결, 계정 USDT 수익/수수료/펀딩/입출금 내역을 GET-only로 확인합니다. 결과는 근거 수집이며 자정 평가액 복원/잠금 해제/주문 실행이 아닙니다. BTC 이력만으로 다른 종목의 과거 포지션을 입증하지 않으므로 recovery_eligible=false를 유지합니다.

전체 테스트 462개, mypy 70개 소스, 전체 Ruff lint/format 통과. 기존 위험/손실 기준/잠금/주문 코드 변경 없음. 실제 Binance 인증·주문 호출 없음. main 변경 없음. 실거래 및 Phase 5 금지.
