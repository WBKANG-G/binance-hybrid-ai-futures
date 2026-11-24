# 원본 정지 범위 점검 v3 — Docker 네이티브 조회 호환성

v2 결과가 CHECK_ORIGINAL_SCOPE / CONTEXT.ps1 7번째 줄로 중단 위치를 좁혔다.
그 줄은 & docker 실행이다. 실제 엔진 연결 상태나 stderr 내용은 아직 확인되지 않았다.
Windows PowerShell5.1은 Stop 설정에서 네이티브 stderr를 예외로 바꿀 수 있으므로,
v3는 .NET Process로 stdout/stderr와 종료 코드를 직접 받는다. stderr 원문은 출력하지 않는다.
Docker가 실제로 실패하면 성공으로 넘기지 않는다. 종료0의 정상 stdout에도 기존 내용/범위
검사를 그대로 적용한다. 네이티브 호출과 UTF8 디코딩 외의 guard 조건은 바꾸지 않는다.

허용 명령은 ps 전체ID조회와 container/image/volume inspect뿐이다. start/create/kill/rm 등은
프로세스 시작 전 거부한다. 실행 파일을 직접 선택하고 shell을 사용하지 않으며 Windows
인자 인용을 처리한다. API 키·시크릿·환경변수/호출자 입력은 인자로 전달하지 않는다.
stdout/행 수/행 길이를 제한하고15초 초과 시 방금 만든 Docker CLI 프로세스만 종료한다.
Docker Desktop/엔진/원본 컨테이너를 자동 시작하거나 현재 context를 바꾸지 않는다.

기존 폴더를 두고 이 v3 폴더에서 RUN_SCOPE_CHECK.cmd 한 번을 실행한다.
SCOPE_DOCKER_DAEMON_UNAVAILABLE이면 엔진/연결 대상 사용 가능 여부를 확인해야 한다.
SCOPE_DOCKER_CLI_NOT_FOUND이면 Docker CLI 설치/검색 경로 확인 대상이다.
이 코드가 특정 원인을 확정하는 것은 아니므로 원본 컨테이너를 임의로 시작하지 않는다.

오류 정보에는 Docker 조회 종류/실행 파일 발견 여부/CLI 실행 여부/종료 코드/stderr 유무만
추가한다. 실제 stderr/사용자 전체 경로/명령 인자/원문 응답은 출력하지 않는다.
BACKUP_PLAN 오류도 숨기지 않고 표시한다. RemoteException은 공개 예외 종류로만 표시한다.
실제 사용자Windows5.1/엔진 상태는 v3 실행 결과를 관측하기 전까지 미검증이다.

고정 백업 영수증·manifest·database/runtime/images 아카이브 해시를 읽고 파일을 RO로 잠근다.
기존 검증된 원본 컨테이너 식별/마운트/이미지/재시작 정책/정지 범위와 보존/복원 helper를
영수증에 묶어 확인한다. 알려진 비교 helper52...는 전체 RO 격리/중지/종료0을 반복 확인한
경우에만 기존 범위 검사에서 제외한다. 이름/라벨로 임의 helper를 허용하지 않는다.

컨테이너/볼륨 생성, 원본 DB 시작, 새 사본 복원, SQL 조회, API 조회, 파일 쓰기를 하지 않는다.
금액/원장/요청도 바꾸지 않는다. Docker의 inspect/조회와 기존 파일 읽기만 한다.
검사 후 모든 입력 해시와 원본 상태를 다시 확인한다. 잘못된 인자나 변조 파일은 실행 전에 거부한다.

성공: ORIGINAL_SCOPE_CHECK_COMPLETE_NO_START_NO_WRITES_NO_ORDERS
성공은 정지 범위/백업 입력 확인이다. 원본 볼륨 파일 자체의 새 바이트 비교나 현재 SQL/fence/
owner·lease 확인,5분 이내 현재 계정 조회,원본 적용 준비 완료를 뜻하지 않는다.
original_apply_ready=false다. 새 출처 재사용의 실제 적합성 평가도 이 도구에서 하지 않는다.
권한/기간/금액/새 조회 조건이 모두 맞아야 추후 출처만 재사용할 수 있다.
실패하면 원본을 계속 중지하고 화면을 확인한다. 원문 파일·키를 공유하거나 자동 재시도하지 않는다.
