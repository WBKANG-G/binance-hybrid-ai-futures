# Phase 4 취소·미체결 주문의 로컬 수량 기록 정정

ZIP을 모두 추출하고 CANCELLED_CORRECTION_START_HERE_KO.md를 읽으세요. 기존 DB가 있는 Galaxybook PC에서 실행합니다.

기본 명령은 변경 전 미리보기입니다. 실제 적용에는 별도 토큰과 고정 작업 ID가 필요하며 다시 근거를 GET 검증합니다. 정정 전 주문 전체와 근거를 감사 기록에 보존하고 잘못 붙은 수량·위험 금액·포지션 상태만 정정합니다. 과거 UNKNOWN/SUBMITTING 요청, 정책·계정 평가액·일/주 손실 기준과 EMERGENCY/paused는 유지합니다.

미리보기: `docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.review_demo_cancelled_correction`

클라우드 테스트는 실제 PostgreSQL과 합성 거래소 응답을 사용했습니다. 실제 사용자의 Binance 인증/주문 및 DB 정정은 수행하지 않았습니다. 검증 결과와 제한은 첨부 문서를 참고하세요. main 변경, 잠금 해제, 실거래, Phase 5 시작 없음.
