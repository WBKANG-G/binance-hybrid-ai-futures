# 전체 체결 내보내기 요청 — Phase 4 근거 수집

기존 DB가 있는 Galaxybook PC에서 실행합니다. 기존 PostgreSQL 데이터·손실 기준·비상 잠금을 유지합니다. 이번 도구는 전체 계정 체결 파일을 요청하기 위한 것이며, 주문 실행/복구 적용/잠금 해제/일별 기준 변경을 하지 않습니다.

공식 Binance 커넥터는 trade export 생성이 UI와 API 합산 월5회로 제한된다고 안내합니다. 데모 서버의 지원과 실제 한도는 사용자 환경에서 아직 확인하지 못했습니다. --request-export는 이 한도를 사용할 수 있는 생성 요청 한 번을 명시적으로 실행합니다. 자동 생성이나 재시도는 없습니다.

1. ZIP을 모두 추출하고 windows-demo-trade-export 폴더에서 infra가 보이는 위치를 엽니다.
2. Docker Desktop을 켜고 탐색기 주소창에 cmd를 입력합니다.
3. 실행합니다. 기존 hybrid-windows_postgres_data는 유지하고, 요청 기록용 hybrid-windows_demo_export_data만 별도로 추가합니다.

    docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait

4. 모두 Healthy면 전체 체결 내보내기를 한 번 요청합니다.

    docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.request_demo_trade_export --request-export

5. DEMO API Key/Secret을 숨김 입력하고 마지막 결과 화면만 공유합니다. 키나 요청 기록 파일은 보내지 마세요.

--request-export 없이 실행하면 생성 요청이나 키 입력 없이 PLAN_ONLY_NO_EXPORT_REQUEST로 조회 계획만 보여줍니다. 요청 구간은 기존 입출금 추적을 시작한 날의 KST00부터 요청 직전 확인한 UTC cutoff까지이며, symbol 필터를 보내지 않습니다. 최대365일로 제한합니다. 현재 계정의 Isolated BTCUSDT 2x/포지션0/활성 주문0과 원장에 있는 과거 진입 주문의 ID·방향·수량·가격·체결량을 대조합니다. 잘못된 키 계정이나 미정리 노출을 발견하면 생성 전에 중단합니다.

JOB_REQUESTED_NOT_DOWNLOADED는 서버가 downloadId를 돌려줬다는 뜻입니다. 파일을 받았거나 이력의 완전성을 검증했다는 뜻은 아닙니다. 생성 요청 전에 로컬 intent를 동기 저장하고 성공 시 ID를 덧붙입니다. 같은 저장 기록이 있으면 ALREADY_REQUESTED_NO_NEW_JOB으로 새 작업을 생성하지 않습니다. 시간초과/잘못된 응답/지원 불명 오류면 intent가 남으며 재생성을 차단합니다.

요청이 정상 접수된 뒤 기존 작업 상태만 확인하는 명령은 아래와 같습니다. 이 명령은 생성 API를 호출하지 않으며 한 번만 조회합니다. 자동 반복 조회하지 않습니다.

    docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.request_demo_trade_export --status-existing

PROCESSING이면 아직 준비 중입니다. LINK_READY_NOT_DOWNLOADED이면 링크가 준비되어 비공개 로컬 기록에 저장됐지만 파일은 받지 않은 상태입니다. 출력에는 URL 전체·경로·쿼리 토큰을 넣지 않고 호스트만 표시합니다. 링크는 공식 커넥터 안내상24시간 만료입니다. 링크의 호스트와 실제 원본 파일 형식은 후속 단계에서 검증해야 하며 임의 링크를 자동으로 따라가지 않습니다.

오류면 생성 요청을 반복하거나 기록/볼륨을 삭제하지 말고 결과 코드만 공유하세요. API가 DEMO에서 지원되지 않을 가능성도 있습니다. 실패를 '요청이 절대 생성되지 않았다'는 뜻으로 처리하지 않습니다. POST/DELETE/PUT export, 실거래 호스트 또는 임의 주소 요청은 허용하지 않습니다.

요청 기록은 /app/demo-export-state/request.json(전용 volume)에 저장합니다. 폴더는0700, 파일은0600이며 컨테이너를 다시 만들어도 volume이 보존되면 기록이 남습니다. 동시 생성도 파일의 배타적 생성으로 하나만 허용합니다. DB/lease/기존 감사 기록에는 쓰지 않습니다. 요청 기록에는 다운로드 URL이 포함될 수 있으므로 파일을 대화에 올리지 마세요. volume 삭제/down -v/임의 기록 초기화/과거 버전으로 되돌리기를 하지 마세요.

이 단계의 모든 출력은 recovery_eligible=false, historical_completeness_verified=false입니다. 원본 체결 파일이 없고 자정 계정 전체 포지션/누적 입출금 기준도 미검증이므로 일별 기준값 복원이나 Phase5로 넘어가지 않습니다.
