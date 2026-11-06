# 전체 체결 내보내기 요청 도구 검증

전체 테스트 539개 통과(73.25초), mypy 76개 소스 통과, 전체 Ruff lint/format 검사 통과. 실제 PostgreSQL과 합성 거래소 응답·로컬 WS 사용. 실제 사용자의 Binance 인증/내보내기 생성/주문 호출은 수행하지 않았습니다. 클라우드에서 export quota를 사용하지 않았습니다.

공식 근거: Binance 공식 GitHub 커넥터 binance/binance-futures-connector-python의 commit a6bfbbf10fe2c1b4eb76fc24ffb82eb94bf9df89, binance/um_futures/account.py의 download_trade_asyn/async_download_trade_id 함수(1000~1041행). GET /fapi/v1/trade/asyn(startTime,endTime), GET /fapi/v1/trade/asyn/id(downloadId), UI/API 공유 월5회 생성, 최대1년 구간, 링크24시간 만료가 명시돼 있습니다. 공식 웹 문서는 이 환경의 CONNECT403 제한으로 열리지 않아 공식 GitHub 소스를 직접 읽었습니다. DEMO 서버 지원과 실제 응답/한도는 아직 확인하지 못했습니다.

새 테스트 30개는 생성 전 intent 파일 동기 저장, 동시 생성 최대1개, 같은 저장 기록으로 재실행 시 새 생성 없음, 시간초과/잘못된 응답/후속 기록 저장 실패 시 intent 유지 및 재생성 금지, 기존 ID 상태 조회와 링크 비출력/비추적, 잘못된 ID/상태/비HTTPS·사용자정보·포트 링크 차단, symlink/권한/손상 기록 차단, 현재 노출·과거 주문 신원 불일치·쓰기 활성 문맥 차단, 생성 API의 symbol 없는 시간 범위와 KST 추적 시작일00부터 cutoff까지의 구간, CLI 기본 계획의 키 입력/DB 변경/lease/생성 없음, 명시적 생성의 DB·이벤트 불변/GET-only/키 정리/오류 마스킹을 확인합니다. export 경로의 POST/PUT/DELETE 및 실거래 전체 URL 요청도 네트워크 전에 차단합니다.

정상 생성 시에도 JOB_REQUESTED_NOT_DOWNLOADED일 뿐이며 원본 파일 확보/과거 체결 완전성/자정 평가액을 검증한 것이 아닙니다. 상태 조회는 한 번만 수행하고 자동 polling/새 생성/실제 링크 다운로드를 하지 않습니다. 링크는 HTTPS 구조만 확인해 전용 비공개 로컬 기록에 보관하고, 출력에는 호스트만 표시합니다. 해당 다운로드 호스트의 허용 여부와 원본 파일 스키마/시간대/기간/계정 범위는 후속 확인이 필요합니다.

요청 파일은 생성 전에 O_EXCL/O_NOFOLLOW로 배타적 생성하고 파일 및 디렉터리를 fsync합니다. receipt 교체는 같은 디렉터리의 임시 파일을 fsync 후 원자 교체합니다. 디렉터리0700/파일0600·소유자·정규 파일 여부를 확인합니다. 별도 named volume으로 컨테이너 재생성 후에도 보존하도록 구성했습니다. Docker 오프라인 검증에서 UID10001, 디렉터리0700, 파일0600 및 두 컨테이너 사이 내용 유지가 통과했습니다. 이 마운트 초기화 실험에는 이미 캐시된 PostgreSQL 이미지를 기반으로 만든 임시 이미지/볼륨을 사용했고 모두 정리했습니다. 실제 Python 앱 이미지는 클라우드에서 재빌드하지 않았으므로 사용자의 Healthy 및 파일 접근 결과로 최종 확인해야 합니다.

변경: DemoClient의 GET allowlist에 공식 export 두 경로 추가, 새 서비스/CLI/통합 테스트/문서, api Dockerfile에 비공개 디렉터리 생성, Compose에 전용 demo_export_data volume 마운트. 새 volume은 hybrid-windows_demo_export_data입니다. 기존 PostgreSQL의 hybrid-windows_postgres_data 및 환경/포트/서비스/위험 설정은 유지합니다. API/web은 PAPER/LIVE=false이며 자동 export worker는 추가하지 않았습니다.

기존 주문/정정/recovery/위험 계산/원장 모델/저장 구현/손실 기준/MASTER_SPEC/AGENTS/의존성 lock/Isolated 설정은 이전 ZIP과 바이트 동일합니다. 내보내기는 사용자 명령으로만 요청합니다. 기존 DemoClient의 서명/redirect 차단/실거래 호스트 차단과 API 키 마스킹을 유지하고 생산 호스트로 fallback하지 않습니다. 사용자 DB/원장/lease/과거 요청/손실 기준/비상 잠금을 바꾸지 않습니다. 별도 export 기록 파일을 저장하는 행위와 서버 보고서 생성 요청은 있습니다.

제한: DEMO가 export API를 지원하지 않거나 권한/한도/응답 오류가 있을 수 있습니다. 결과가 불명확하면 이를 생성되지 않았다는 뜻으로 처리하지 않고 기록을 보존하여 자동 재생성을 막습니다. 기록 volume을 삭제하거나 다른 volume/PC를 사용하면 로컬 중복 방지를 잃으므로 기존 PC·프로젝트·volume을 유지해야 합니다. 이 도구만으로 월 전체 quota 사용 횟수를 조회/확정하지는 않습니다. 현 단계는 항상 recovery_eligible=false/historical_completeness_verified=false이며, 실제 파일을 받은 뒤 범위·내용·입출금/자정 평가액을 추가 검증해야 합니다. main 변경, 실거래, Phase5 진입 없음.

공식 소스 URL: https://github.com/binance/binance-futures-connector-python/blob/a6bfbbf10fe2c1b4eb76fc24ffb82eb94bf9df89/binance/um_futures/account.py#L1000
