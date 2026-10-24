# Phase4: 자정 전 실제 청산·자정 후 로컬 대조에 대한 일별 기준 검토

ZIP을 추출하고 EXIT_DAY_START_HERE_KO.md부터 읽으세요. 기존 Galaxybook PC의 같은 hybrid-windows 프로젝트/DB/export 볼륨을 유지합니다.

기본 미리보기 명령: `docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.review_demo_exit_day --attest-no-activity-since-exit`

사용자 확인과 서명된 체결·income·현재 USDT 자산을 결합하는 수동 출처 경로입니다. account_wide_exchange_export_verified=false입니다. 미리보기는 GET-only/DB 무변경이며, 실제 일별 기준 채택에는 별도의 수용/토큰/작업 ID가 필요합니다. 관측 시각을 과거로 바꾸거나 비상 잠금/일시정지를 해제하지 않습니다.

전체628개 테스트, mypy80개 소스, Ruff 통과. 클라우드 검증은 합성 거래소/실제PostgreSQL/로컬WS이며, 실제 DEMO 추가 주문이나 사용자 DB 적용은 수행하지 않았습니다. 기존 자동 청산 확인 수정과 자료는 포함되어 있습니다. 신규 주문, 초기화, export 재요청, LIVE/Phase5 진입 및 main 변경 없음.
