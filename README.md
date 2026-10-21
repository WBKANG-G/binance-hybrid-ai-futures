# Windows Phase 4 오늘 기준 복원 근거 수집

ZIP을 모두 추출하고 DAY_REVIEW_START_HERE_KO.md를 읽으세요. 설치 후 `docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.review_demo_day_boundary`를 실행합니다. DEMO 키는 숨김 입력합니다.

오늘 KST 0시부터의 수익/수수료/펀딩/입출금 내역과 기존 DB 자정 평가액 기록을 읽습니다. 지갑 역산 후보는 승인된 손실 기준이 아닙니다. 과거 미실현 손익과 누적 입출금 근거도 별도로 검증해야 합니다. recovery_eligible=false이며 DB·잠금·과거 UNKNOWN·손실 기준을 변경하지 않습니다.

전체 테스트 442개 통과, mypy 68개 소스, 전체 Ruff lint/format 통과. 실제 Binance 인증/주문 호출 없음. main 변경 없음. 실거래와 Phase 5 금지.
