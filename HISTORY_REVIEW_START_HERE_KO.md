# 마지막 종료 이후 이력 확인 — 기존 DB가 있는 이전 PC에서 실행

새 PC의 빈 DB에서는 실행하지 마세요. 이전 PC의 기존 DB와 EMERGENCY/paused/미확정 기록을 유지합니다. 이번 단계는 거래소 GET 조회와 로컬 DB 읽기만 하며, 기준값 저장·복구 적용·주문 실행을 하지 않습니다.

1. ZIP을 모두 추출하고 windows-demo-history-review 폴더에서 infra가 보이는 위치로 들어갑니다.
2. Docker Desktop을 켜고 탐색기 주소창에 cmd를 입력한 뒤 Enter를 누릅니다.
3. 설치 명령을 실행합니다. 기존 hybrid-windows 프로젝트와 DB 볼륨을 사용합니다.

    docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait

4. 모두 Healthy면 아래 조회 명령을 실행합니다.

    docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.review_demo_flat_history

5. DEMO API Key와 DEMO Secret을 차례로 붙여넣고 Enter를 누릅니다. 입력 내용은 보이지 않는 것이 정상입니다. 키를 대화에 보내지 마세요.
6. 마지막 결과를 대화에 보내주세요. FLAT_HISTORY_EVIDENCE_ONLY는 근거 수집 완료이며 복구 완료가 아닙니다. 오류면 반복 실행하지 말고 화면만 보내주세요.

주요 결과:
- last_known_full_exit_fill_found: 기존 종료 주문과 실제 체결 ID/방향/수량을 대조했는지 여부.
- btc_trades_after_known_exit_before_boundary: 확인된 종료 매도 이후부터 오늘 0시 전까지의 BTCUSDT 추가 체결 건수. 종료 체결을 찾지 못하면 null이며 0으로 간주하지 않습니다.
- other_trades_at_exit_timestamp: 종료 체결과 같은 밀리초의 다른 주문 체결. 정확한 선후 관계는 단정하지 않습니다.
- btc_trades_at_or_after_boundary: 오늘 0시 이후 BTCUSDT 체결 건수.
- account_income_counts: 조회 구간의 계정 USDT 수익/수수료/펀딩/입출금 건수. 자정 전후 건수도 구분합니다.
- wallet_change_matches_income: 기록상 마지막 flat 관측 이후의 지갑 변화와 해당 구간 income 합계가 맞는지 여부. 지연되거나 누락된 기록은 별도 검토가 필요합니다.

기존 종료 직후와 마지막 flat 관측 사이의 구간이 빠지지 않도록 진입 신호의 닫힌 봉 시각까지 조회 범위를 넓혀 실제 종료 체결도 찾습니다. 조회는 최대 7일 구간으로 제한하고 1일 이하 구간으로 나눕니다. 한 응답이 한도 1000건이면 시간 구간을 재분할하여 조회합니다. 같은 밀리초에 한도만큼 몰리거나 요청 예산을 넘으면 누락을 추정하지 않고 중단합니다. 같은 이력을 재조회해 변화 여부도 확인합니다.

BTCUSDT 체결 조회만으로 모든 다른 종목의 과거 포지션이 없었다고 증명할 수는 없습니다. 따라서 account_wide_position_history_verified와 recovery_eligible은 false입니다. 이 결과만으로 자정 계정 평가액을 확정하거나 일별 기준을 쓰지 않습니다. 과거 UNKNOWN도 성공/실패로 바꾸지 않습니다.

API/web PAPER, LIVE_TRADING_ENABLED=false 유지. 실거래 및 Phase 5 금지. --apply-review, 예전 주문 명령, 새 ledger, volume 삭제, down -v를 사용하지 마세요. 실제 .env·키·.git·캐시·의존성 폴더는 ZIP에 포함하지 않습니다.
