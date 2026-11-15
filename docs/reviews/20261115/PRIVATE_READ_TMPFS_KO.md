# 복사본용 읽기 전용 임시 자격증명 경계

PrivateReadCredentialLease 후보를 추가했다. 기존 key-session과 원본 컨테이너를 시작하지 않고 합성 자격증명만 사용해 파일 접근, 같은 자격증명의 수명, 복사본 helper에 허용할 저장소 범위를 검증했다. 실제 Windows·실제 키 입력·거래소 HTTP는 수행하지 않았다.

기존 credentials/ensure 함수는 환경 변수의 DEMO 키를 우선 사용할 수 있다. 새 경로는 해당 fallback을 사용하지 않는다. DEMO/일반/LIVE 키 환경 변수 중 값이 하나라도 있으면 거부한다. 명시적 copy_only/enable_private_read 후 고정 경로 /run/demo-keys가 tmpfs이고 ro/noexec/nosuid/nodev인지 확인한다. 디렉터리와 파일은 현재 UID 소유의 0700/0600이어야 하며 파일은 일반 파일·링크 수 1·4KiB 이하·session.json 한 개만 허용한다.

디렉터리 inode에 shared flock을 잡아 기존 등록/삭제의 exclusive flock과 충돌시킨다. 파일을 읽기 전용으로 열고 핸들을 유지한다. 기존 12시간 만료 포맷의 version/created/expires를 엄격히 확인하고 중복 JSON 키를 거부한다. 대체 키를 등록하거나 만료를 연장하지 않는다. 파일·경로 inode, 권한·크기·수정 시각, RAM 내부 HMAC 및 자격증명 pair가 같고 UTC/단조 시계·boot/PID가 유지되는 동안만 사용한다. 사용 문맥은 최대 75초이며 이후 재사용을 거부한다.

capture_with_private_copy_credentials는 같은 파일의 자격증명을 LocalReadCredentialContext와 V2 archive에 연결한다. 실제 sender는 내장하지 않는다. 테스트에서만 합성 sender를 주입했다. scope 불일치·만료·전송 오류·파일/키 교체 시 문맥과 핸들을 닫고 부분 기록을 보존하며 재전송하지 않는다. 파일을 수리하거나 갱신하지 않는다. 직접 lease를 사용하는 호출자는 with 또는 close로 닫아야 한다. 타이머가 별도 스레드에서 잠금을 자동 해제하는 기능은 없다.

호스트의 순수 검사 함수 validate_private_read_scope는 외부 제어기가 명시한 fresh ID/볼륨과 금지 원본 목록을 받는다. 이 함수가 Docker를 호출하거나 fresh 여부를 독립적으로 발견하지는 않는다. 허용 역할은 다음과 같다.

- key-holder: 읽기 전용 키 tmpfs 한 개.
- registrar: 쓰기 가능한 키 tmpfs 한 개만. DB/runtime/socket은 없다.
- read-helper: 읽기 전용 키 tmpfs와 소켓, 쓰기 가능한 새 복사본 runtime.

세 역할 모두 고정 이미지, UID 10001:10001, network none, 읽기 전용 루트, cap drop ALL, no-new-privileges, restart no, 비 host PID/IPC, 제한된 메모리/PID, 추가 그룹·장치·포트 없음과 log driver none을 요구한다. command/entrypoint는 고정된 Python 실행 형태만 허용하고 자격증명 환경 변수는 거부한다. 원본 볼륨 alias, Docker socket, 추가 마운트, 읽기 helper의 RW 키 마운트는 거부한다. 실제 키 값은 환경 변수·명령행·Docker 로그에 넣지 않는다.

실제 클라우드 Docker에서 새 named tmpfs, holder, 합성 등록 helper와 읽기 helper를 만들었다. 게스트에서 실제 ro tmpfs·소유자·권한을 검사하고 서명을 확인했다. 읽기 lease가 열린 동안 별도의 RW registrar의 clear가 flock으로 차단됐다. 명시적으로 닫은 뒤 테스트 제어기의 clear/register가 성공했고, 만료된 시각은 거부됐다. 마지막 holder가 종료되어 tmpfs가 unmount된 뒤 새 helper에서 session.json이 사라진 것을 확인했다. 기존 컨테이너·볼륨 목록은 보존하고 테스트 소유 자원만 정리했다.

실제 Docker 검사의 runtime/socket 볼륨은 마운트 범위용 빈 새 볼륨이다. PostgreSQL archive 연결은 별도 합성 파일/마운트 대체를 사용하는 PG/SQLite 통합 테스트에서 확인했다. 실제 Docker PostgreSQL 복사본과 tmpfs 자격증명을 동시에 연결하는 전체 실행기 검증은 다음 범위다. Windows Docker Desktop의 실제 실행이나 실제 키 등록 절차는 아직 검증하지 않았다.

이 통제는 같은 UID/root의 메모리·파일 접근, 호스트 swap/메모리 스냅샷까지 보호했다고 주장하지 않는다. close도 모든 Python/OS 메모리의 안전한 삭제를 뜻하지 않는다. 파일의 권한·연속성을 확인해도 그 키의 실제 원격 계좌 신원이나 DEMO 계좌 소유권이 증명되는 것은 아니다. 원격 신원·날짜 채택·설치·주문 승인은 계속 false다.

테스트 58개가 통과했다. 게스트 파일/만료·PG archive 36개(6.32초), 호스트 범위·실제 Docker 22개(29.07초)다. ruff와 후보 두 모듈의 mypy도 통과했다. 상세 범위 및 단계별 한계는 PRIVATE_READ_VALIDATION.json과 PRIVATE_READ_DOCKER_VALIDATION.json에 기록했다.

다음은 새 PostgreSQL 복사본과 이 임시 키 경계를 함께 묶는 합성 실행기 검증이다. 아직 Windows에서 실행할 추가 명령이나 실제 키 입력은 필요하지 않다. 원본·기존 복구 사본 정지와 USB 분리 상태를 유지한다.
