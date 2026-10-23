# Phase 4 전체 체결 내보내기 요청 도구

ZIP을 모두 추출하고 TRADE_EXPORT_START_HERE_KO.md를 읽으세요. 기존 DB가 있는 Galaxybook PC에서 실행합니다.

기본 실행은 계획만 보여줍니다. --request-export는 전체 체결 생성 요청 한 번을 명시적으로 보냅니다. 공식 커넥터 안내상 UI/API 공유 월5회 생성 제한이며, DEMO 지원 여부는 아직 미확인입니다. intent를 먼저 별도 전용 volume에 보존하고 중복 생성/실패 자동 재요청을 차단합니다. --status-existing은 기존 ID의 상태만 조회합니다. 다운로드 URL은 화면에 출력하거나 자동으로 따라가지 않습니다.

기존 PostgreSQL volume/계정 상태/요청/손실 기준/비상 잠금은 보존합니다. 원본 파일을 아직 받거나 검증한 것이 아니므로 이 단계로 기준값을 복원하거나 잠금을 해제하지 않습니다. 실제 사용자의 Binance 호출은 클라우드에서 수행하지 않았습니다. main 변경, 실거래, Phase5 시작 없음.
