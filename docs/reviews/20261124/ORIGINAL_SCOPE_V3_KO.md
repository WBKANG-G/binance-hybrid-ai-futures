# 원본 사전 점검의 Docker 네이티브 조회 v3

v2 사용자 화면에서 CHECK_ORIGINAL_SCOPE,CONTEXT.ps1:7,exception_type=UNKNOWN이 보고됐다.
해당 줄은 & docker @Arguments 2>$null 네이티브 실행이다. 따라서 실패 위치는 Docker 실행으로
좁혀졌지만 사용자 stderr/실제 종료 코드/엔진 상태는 아직 확인하지 못했다. 실제 원인이
엔진 중지인지 경고 출력인지 다른 실행 오류인지는 확정하지 않는다.

Windows PowerShell5.1은 ErrorActionPreference=Stop 아래에서 네이티브 stderr를
NativeCommandError/RemoteException으로 처리해 종료 코드를 확인하기 전에 중단할 수 있다.
v3는 Docker 조회에 한해 .NET Process의 stdout/stderr/exit code를 직접 받는다.
이는 원인군에 대한 호환성 수정과 진단 강화이며 실제 사용자 Win5.1에서 원인을 재현했다는
주장은 아니다. 실제 오류 원문을 받거나 출력하지 않고 가능한 안전 코드로 분류한다.

실행 허용은 정확한 ps --all --quiet --no-trunc,컨테이너 inspect --format ID,
image inspect --format {{.Id}} sha256,volume inspect --format 이름이다. 바꾸기 명령과
옵션을ID로 대체하는 호출은 프로세스 시작 전에 거부한다. Get-Command Application으로
실행 파일을 찾고 UseShellExecute=false를 사용한다. Windows 인자 인용으로 공백/인용부호/
역슬래시를 보존한다. 셸/PowerShell 명령 문자열 평가를 사용하지 않는다.

stdout/stderr는 동시에 읽고15초 실행 제한을 둔다. 시간 초과 시 방금 시작한 Docker CLI
프로세스 객체만 종료한다. 원본 컨테이너나 Docker Desktop/엔진은 시작·종료하지 않는다.
출력 전체/행 수/행 길이도 제한한다. stderr가 있어도 종료0이면 기존 stdout 의미/형식 검사를
진행한다. 종료가0이 아니면 절대 성공으로 처리하지 않는다. 실패한 조회를 자동 재시도하지 않는다.
stderr는 메모리에만 받고 화면/파일에 저장하지 않는다.

진단 출력은 LIST_CONTAINERS/INSPECT_CONTAINER/INSPECT_IMAGE/INSPECT_VOLUME,실행 파일
발견 여부,CLI 시작 여부,숫자 종료 코드,stderr 유무와 고정 실패 코드만 포함한다.
연결 불가의 알려진 문구는 SCOPE_DOCKER_DAEMON_UNAVAILABLE로 분류하지만 원인을
엔진 중지라고 단정하지 않는다. 실행 파일 없음,시작 실패,시간 초과,출력 초과,기타 nonzero도
따로 중단한다. BACKUP_PLAN 오류를 catch가 다시 숨기지 않도록 추가했다. 다른 네이티브
호출에서 RemoteException이 나면 공개 종류 이름만 표시할 수 있게 했다.

Context/Common의 네이티브 실행 함수 본문만 같은 managed reader로 연결하고 기본 행 수
128/256은 보존했다. 나머지 Context/Common 코드와 영수증/마운트/정지/RO/원본 비교 guard는
유지한다. 함수가 반환하는 stdout 배열을 원래 guard가 그대로 검사하므로 오류를 무시해
범위를 넓히지 않는다. 새 프로세스는 Docker CLI의 읽기 명령이며 컨테이너 생성/시작이 아니다.

검증은 실제 PowerShell7과 합성 Docker 실행 파일을 사용한다. 종료0+stderr,빈 정상 출력,
엔진 연결 실패,기타 nonzero,너무 많은/긴 출력,시간 초과,실행 실패,실행 파일 없음,
변경 명령/옵션 대체 거부,공백/인용부호/역슬래시 보존을 확인한다. stderr의 비밀 sentinel이
출력되지 않으며 변경 명령은 가짜 실행 파일조차 시작하지 않아야 한다. 실제 사용자 Windows5.1/
Docker 엔진은 실행 관측 전까지 미검증이다. 실제 사용자 원본/백업 파일이나 Binance API는 읽지 않았다.

이전 v1/v2 폴더는 보존하고 별도 v3 폴더를 실행한다. Docker Desktop 상태는 확인 대상이지만
도구가 자동으로 엔진이나 앱 컨테이너를 켜지 않는다. 원본 쓰기/권한 인수/현재 SQL/fence/
새 계정 조회/거래 재개 권한은 계속 없다. 실제 v3 오류 분류가 있으면 그 결과에 맞춰 다음 조치를 정한다.
