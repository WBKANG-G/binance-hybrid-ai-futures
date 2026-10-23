# Phase 4 취소 주문 수량 오배정 수정 / 읽기 전용 이력 검토

ZIP을 모두 추출하고 CANCELLED_REVIEW_START_HERE_KO.md를 읽으세요. 기존 DB가 있는 이전 PC에서만 실행합니다.

수정본은 종료·미체결 주문에 새 포지션이 붙는 비상 처리 경로를 차단합니다. 기존 DB의 잘못된 수량은 자동 수정하지 않습니다. 새 도구는 거래소에서 취소·체결량 0을 원래 ID로 반복 검증하고 분석용 사본으로만 이력을 조회합니다. 원본 DB, 미확정 요청, 손실 기준, EMERGENCY/paused는 보존합니다.

실행: `docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.review_demo_cancelled_history`

실제 Binance 인증·주문 호출은 클라우드에서 수행하지 않았습니다. 검증 결과와 제한은 첨부 문서를 참고하세요. main 변경, 잠금 해제, 실제 주문, Phase 5 시작 없음.
