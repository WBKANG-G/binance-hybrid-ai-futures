# Windows Phase 4 복구 사전 검토

ZIP 다운로드 → 모두 추출 → RECOVERY_START_HERE_KO.md를 읽으세요. 이번 단계는 설치 및 기본 검토만입니다.

기본 명령: `docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.review_demo_recovery`

DEMO 키를 숨김 입력하고 GET-only 조회 및 DB 읽기로 검토 토큰을 만듭니다. 기본 명령은 lease/DB 변경/잠금 해제/새 주문을 하지 않습니다. --apply-review 등의 적용 인수는 이번 단계에서 사용하지 마세요. 기존 미확정 요청과 EMERGENCY/paused를 보존합니다.

명시적 적용 기능은 별도 토큰/작업 ID/미확정 이력 인지 확인을 모두 요구하며, 적용 자체는 주문 없이 새 진입 일시정지를 유지하고 감사 이벤트를 기록합니다. 실제 적용은 아직 하지 않았습니다.

전체 테스트 412개 통과, mypy 66개 소스 및 전체 Ruff lint/format 통과. 로컬 PostgreSQL/합성 응답/로컬 WS로 검증. 실제 Binance 인증·주문 호출, Windows 복구 적용, main 변경, Phase 5 시작 없음. API/web PAPER 및 LIVE false. 실제 .env·키·캐시·의존성·.git 제외.
