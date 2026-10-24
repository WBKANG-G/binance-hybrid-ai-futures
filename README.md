# Phase4 DEMO income 시각 정밀도 차이 수정

ZIP을 풀고 INCOME_TIME_START_HERE_KO.md부터 읽으세요. 기존 Galaxybook PC의 새 windows-demo-income-time-fix 폴더에서 같은 hybrid-windows 프로젝트로 실행합니다. 기존 DB/볼륨/잠금/복구 기록을 보존합니다.

명령은 기존과 같습니다: `docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.review_demo_exit_day --attest-no-activity-since-exit`

밀리초 체결과 같은 초 시작으로 기록된 income의 개별 수수료·손익을 대조합니다. 임의 시간 오차를 허용하지 않으며 같은 초의 다른 체결·추가/상쇄/불일치 기록은 차단합니다. 정확한 체결 시각과 실제 관측 시각을 유지하고 출처·대조 방식을 표시합니다.

전체650개 테스트/mypy80개 소스/Ruff 통과. 부모 구현에서 실제 응답 형태의 동일 오류 재현 후 수정 검증했습니다. 사용자 확인 기반 수동 출처이며 account_wide_exchange_export_verified=false입니다. 기본 미리보기는 GET-only/DB 무변경이고 실제 기준 저장에는 별도 수용·토큰·작업 ID가 필요합니다. 신규 주문, 초기화, export 재요청, 잠금 해제, LIVE/Phase5나 main 변경은 하지 않습니다.
