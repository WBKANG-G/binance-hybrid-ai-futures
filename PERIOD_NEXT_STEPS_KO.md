# 다음 실행: 한국시간 날짜 경계 읽기 전용 확인

현재 사용 중인 windows-demo-empty-db-restart 폴더의 CMD를 유지합니다. 이 보고서 ZIP은 실행 폴더를 교체하는 배포본이 아닙니다.

한국시간2026-11-01 00:00 이후 아래 명령을 실행합니다. 거래소 키를 읽거나 요청을 보내지 않으며, DB를 읽기 전용으로 조회합니다. 리더를 획득하거나 장부/기준금/잠금을 변경하지 않습니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.review_demo_recovery --local-periods
```

전체 JSON을 공유합니다. period_keys_match, stored_day_kst/current_day_kst, stored_week_kst/current_week_kst, risk_state/paused를 확인합니다. false는 날짜 경계에서 발생할 수 있는 진입 차단 조건이며, 기준금을 현재 잔고로 초기화하라는 뜻이 아닙니다. 새 주문/이전 토큰 적용/추가 복구 승인을 하지 않습니다. 확인 후 기존 서명 이력·기준금 검토 경로를 선택합니다. 코드가 이미 기준금을 자동 전환했다고 추정하지 않습니다.

현재 알려진 상태는 마지막 승인 후 NORMAL/paused, 신규 진입/LIVE 비활성입니다. 별도 실제 부분체결·TP 트리거·실행 worker 장애 등은 PHASE4_REVIEW_KO.md의 미완료 목록을 따릅니다.
