# Phase 4 오류 진단 수정본 — 새 주문 실행 금지

이 버전은 최초 체결 직후 실패 원인이 해결됐다는 뜻이 아닙니다.
종료/종료 대기 또는 보유 수량 0인 포지션의 손절·익절 전송을 차단합니다.
새로 발생하는 상태 대조 오류는 제한된 원인 분류만 기록하며 원문·키를 저장하지 않습니다.
기존 DB의 과거 오류 원인은 소급 복원할 수 없습니다.

1. ZIP을 모두 추출하고 windows-demo-reconcile 폴더에서 infra가 보이는지 확인합니다.
2. Docker Desktop을 켭니다. 탐색기 주소창에 cmd를 입력하고 Enter를 누릅니다.
3. 아래 설치 명령을 실행합니다. 프로젝트 이름을 바꾸지 않아 기존 DB를 유지합니다.

    docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait

4. Healthy 후 아래 DB 조회 명령만 실행합니다. API 키 입력은 없습니다.

    docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.diagnose_demo_ledger

5. 결과를 대화에 보내주세요. 이는 로컬 DB 조회이며 거래소 주문·취소·연결을 하지 않습니다.

이전 시작 안내서와 주문 실행 예시는 개발 이력입니다. 지금 실행하지 마세요.
기존 EMERGENCY와 paused 잠금은 해제하지 않습니다. 신규 operation ID를 만들지 않습니다.
실거래 금지, Phase 5 미시작. API/web은 PAPER, LIVE_TRADING_ENABLED=false입니다.
DB 볼륨 삭제 및 down -v를 실행하지 마세요. API 키·실제 .env는 포함하지 않습니다.

검증: 이전 수정 테스트 365개 통과. 이번 진단 스크립트는 별도 DB 읽기 전용 테스트로 확인합니다.
Windows에서의 설치/진단은 사용자가 확인해야 합니다.
