# Phase 4 읽기 전용 preflight 수정

TUSDT의 잘못된 종목 필터가 모든 계좌 조회를 막는 문제를 수정했습니다.
읽기 전용에서는 INVALID_SYMBOL_FILTERS로 제외하고 결과의 excluded_symbols에 표시합니다.
주문 활성화 실행은 유효하지 않은 필터가 있으면 여전히 전체 시작을 거부합니다.
실거래 및 Phase 5는 금지하며 DEMO 인증 성공/Phase 4 완료를 주장하지 않습니다.

Docker Desktop을 켠 뒤 이 폴더의 주소창에 cmd를 입력하고 Enter를 누릅니다.
아래 명령으로 기존 hybrid-windows 프로젝트의 이미지를 갱신합니다.

docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait

기존 DB 볼륨 hybrid-windows_postgres_data는 같은 프로젝트 이름으로 재사용합니다.
볼륨 삭제, down -v, 원장 삭제, API 키를 파일에 기록하는 작업은 하지 마세요.
새 폴더에는 개인 키나 실제 .env를 복사하지 마세요.
기존 검증 원장은 유지되며 인증을 다시 확인할 때 --initialize 없이 기존 원장을 사용합니다.

변경: services/execution/demo.py, scripts/run_demo.py 및 회귀 테스트 1개.
검증: 관련 pytest 31 passed; Ruff check/format 및 mypy 통과.
실제 Windows 새 이미지 실행/DEMO 인증은 사용자 PC에서 후속 확인이 필요합니다.
