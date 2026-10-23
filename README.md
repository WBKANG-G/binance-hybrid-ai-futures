# Phase 4 사용자 확인을 포함한 USDT 일별 기준 복원 미리보기

ZIP을 추출하고 DAY_RECONSTRUCTION_START_HERE_KO.md를 읽으세요. 기존 DB가 있는 Galaxybook PC에서 실행합니다.

이 경로는 추가 계정 거래가 없다는 사용자 확인과 서명된 BTC 종료·빈 income 구간·일치하는 USDT 잔액을 결합합니다. 거래소 계정 전체 export를 검증했다고 표시하지 않습니다. 기본 명령은 변경 없는 미리보기이며 실제 적용에는 별도 토큰/작업 ID/수동 근거 수용 옵션이 필요합니다.

미리보기: `docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.review_demo_attested_day --attest-no-other-trades-since-exit`

조건 불일치면 차단합니다. 일별 기준 채택 후에도 주별 기준/고점/기존 valuation 시각/과거 요청·주문·감사·export 기록과 EMERGENCY/paused를 유지합니다. 계정 전체 거래소 원본 검증은 계속 false입니다. 실제 사용자의 DB 채택은 클라우드에서 수행하지 않았습니다. main 변경, 실거래, Phase5 진입 없음.
