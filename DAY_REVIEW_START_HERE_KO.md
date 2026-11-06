# 오늘 손실 기준 복원 가능성 — 읽기 전용 근거 수집

이 단계는 기존 DB와 손실 기준값을 변경하지 않습니다. 안전 잠금 해제, 새 주문, Phase 5도 진행하지 않습니다.

1. ZIP을 모두 추출하고 windows-demo-day-review 폴더에서 infra가 보이는 위치로 들어갑니다.
2. Docker Desktop을 켜고 탐색기 주소창에 cmd를 입력한 뒤 Enter를 누릅니다.
3. 아래 설치 명령을 실행합니다. 프로젝트 이름과 DB 볼륨은 기존과 같습니다.

    docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait

4. 모두 Healthy면 아래 명령을 실행합니다.

    docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.review_demo_day_boundary

5. DEMO API Key와 DEMO Secret을 차례로 붙여넣고 Enter를 누릅니다. 화면에 입력 내용이 보이지 않는 것이 정상입니다. 키를 채팅에 보내지 마세요.
6. 마지막 결과를 대화에 보내주세요. DAY_BOUNDARY_EVIDENCE_ONLY는 근거 수집 완료이며 복구 완료가 아닙니다. 오류면 반복 실행하지 말고 결과만 보내주세요.

도구는 오늘 한국 시각 0시부터의 수익/수수료/펀딩/입출금 등 USDT income 내역을 페이지별로 읽고 같은 구간을 재조회합니다. 현재 계정/주문 상태가 조회 중 변하지 않고 비어 있는지 확인합니다. 원래 DB 이벤트에서 정확히 0시의 평가액이 기록된 사례도 제한된 범위에서 찾습니다.

wallet_candidate_usdt_not_approved는 현재 지갑 잔액에서 조회된 순 income을 뺀 역산 후보입니다. 이것만으로 자정 계정 평가액이나 입출금 조정 손실 기준을 확정할 수 없습니다. 자정 미실현 손익과 자정 시점 누적 입출금 기준은 별도로 검증해야 합니다. income 지연·보존 범위·API 내역만으로 입증되지 않는 활동도 검토가 필요합니다. 따라서 recovery_eligible은 항상 false이며 기준을 DB에 쓰지 않습니다. 당시 포지션이 없었다고 자동 가정하지 않습니다.

API/web PAPER, LIVE_TRADING_ENABLED=false. 기존 EMERGENCY/paused/UNKNOWN 기록은 보존됩니다. --apply-review, 예전 주문 명령, 새 ledger, volume 삭제, down -v를 사용하지 마세요. 이 ZIP은 실제 .env·API 키·.git·캐시·의존성 폴더를 포함하지 않습니다.
