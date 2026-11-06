# Phase 4 상태 대조 수정본 — 설치 후 DB 조회만 실행

이번 수정은 포지션이 있는 두 조회에서 주문·수량·진입가·레버리지·담보 배정액·입출금이 같고 평가값만 다른 경우를 구분합니다. 계정 평가액과 사용 가능 잔액은 작은 값, 사용 증거금은 큰 값, 청산가는 포지션 방향에 불리한 값을 사용합니다. 포지션 없는 신규 진입 검사와 필수 필드 검사는 완화하지 않습니다. 과거 실제 오류 원인은 기록 부족으로 미확정입니다.

1. ZIP을 모두 추출합니다. windows-demo-stable 폴더에서 infra 폴더가 보이는 위치로 이동합니다.
2. Docker Desktop을 켜고 탐색기 주소창에 cmd를 입력해 Enter를 누릅니다.
3. 아래 설치 명령을 실행합니다. 기존 hybrid-windows 프로젝트와 DB를 그대로 사용합니다.

    docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait

4. 모두 Healthy면 아래 로컬 DB 조회만 실행하고 결과를 대화에 보내주세요. API 키를 입력하지 않습니다.

    docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.diagnose_demo_ledger

설치는 거래소 주문을 보내지 않습니다. DB 조회는 PostgreSQL 읽기 전용 트랜잭션입니다. 기존 EMERGENCY 및 paused 잠금은 해제하지 않습니다. 예전 주문 실행 명령과 새 operation ID를 실행하지 마세요. 다른 시작 안내서는 개발 이력입니다.

LIVE_TRADING_ENABLED=false와 API/web PAPER를 유지합니다. 실거래 금지, Phase 5 미시작. 키/실제 .env는 포함하지 않습니다. 기존 폴더와 DB를 보존하고 volume 삭제 및 down -v를 실행하지 마세요.

Windows 실제 설치 결과는 확인 대기입니다. 로컬 합성 거래소 재현 테스트 통과가 실제 DEMO 손절/익절 검증 완료를 뜻하지 않습니다.
