# Phase4 빈 계좌 DB 서버 재시작·장부 보존 점검

새 windows-demo-empty-db-restart 폴더에 ZIP을 풀고 EMPTY_DB_START_HERE_KO.md 순서대로 진행합니다. 먼저 빌드 Healthy → DEMO 키 RAM 등록 → 문서 UUID로 --prepare만 실행하고 결과를 공유합니다. PREPARED_EMPTY_NO_ORDERS와 leader_released true를 확인한 뒤에만 DB 컨테이너 restart db → up -d --wait → 같은 UUID --verify 순서로 진행합니다. 도구는 Docker를 직접 제어하지 않습니다.

주문/포지션/마진이 없는 NORMAL/paused 계좌만 허용합니다. 각 단계에서 두 번의 GET 스냅샷을 비교하고 장부 전체(운영 플래그2개와 자체 표식만 제외)의 해시, 원격 상태 해시, PostgreSQL 실제 시작 시각, 펜스를 기록/대조합니다. UNKNOWN/NOT_SENT 및 모든 이력·기준금·회계를 보존합니다. 재시작이 실제 관찰되고 다음 리더 펜스가 정확히1 증가해야 하며, 다른 작업/현금/기준금/노출 변경은 차단합니다. 각 단계의 리더 반환까지 확인합니다. 원격 요청은 GET만 하며 신규 진입/청산/보호 주문·listenKey 생성은 없습니다.

전체1228개·집중22개 시험, Ruff 및 mypy110개 소스 통과. 별도의 실제 격리 PostgreSQL 컨테이너 재시작 시험도1개 통과했습니다. 거래소는 합성 SDK이며 클라우드 인증 거래소 호출은 없습니다. 기존403개 파일과 테스트는 모두 동일하고 별도 도구·시험·한국어 문서/최근 수락 증거10개만 추가했습니다. 새 스키마/설정/패키지/Docker 소켓 권한을 추가하지 않았습니다.

이번 범위는 빈 계좌의 유휴 DB 정상 재시작 및 장부 보존입니다. 실행 중 worker/트랜잭션 장애, 강제 전원 손실/호스트 재부팅, 실제 부분체결 보호 또는 Phase4 전체 완료로 확대하지 않습니다. 별도 전체 복구 감사 및 WSS 점검을 대신하지 않습니다. 현재 NORMAL/paused를 유지하고 기본 진입을 재개하지 않습니다. Windows 실제 재시작은 아직 검증 전입니다. main/LIVE/Phase5 변경 없음.
