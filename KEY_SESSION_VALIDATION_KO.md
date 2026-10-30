# 휘발성 DEMO 자격 증명 재사용 검증

## 변경 범위

전용 scripts/demo_key_session을 추가했습니다. 명시적 --register/--status/--clear만 제공하며 등록은 숨김 입력, 상태·삭제는 값 출력 없이 동작합니다. /run/demo-keys 전용 tmpfs가 실제 /proc/self/mountinfo에서 tmpfs이고 noexec/nosuid/nodev인지, 디렉터리 소유자/0700과 일반 파일 소유자/0600·단일 hardlink·크기·형식·12시간 유효기간을 확인합니다. 일반 디스크/바인드 마운트·symlink·FIFO·손상 파일·시계 역행/만료는 차단합니다. 파일은 O_EXCL/O_NOFOLLOW로 만들고 기존 등록을 조용히 교체하지 않습니다.

Docker Compose의 API에만 tmpfs 메모리 마운트를 추가했습니다. 기존 DB/export 볼륨과 빌드/환경·바인딩·서비스 명령은 그대로입니다. 기존 review_demo_* CLI8개와 run_demo에 등록 값 로더를 연결했습니다. 기존 직접 런타임 키 우선과 일반/LIVE 키 혼용 차단을 유지하며, 조회 CLI의 미등록 숨김 입력도 보존합니다. run_demo는 미등록 상태에서 키를 묻거나 키 등록을 자동으로 하지 않습니다. 기존 SDK/from_environment와 DEMO 고정 호스트는 그대로입니다. 이 기능의 키 값을 환경 변수로 새로 설정하지 않고 SecretStr로 클라이언트에 전달합니다. 기존 정리 finally 블록은 유지합니다.

tmpfs 파일은 메모리 안의 평문이며 같은 사용자/관리자 접근을 막는 보안 영역은 아닙니다. 호스트 swap/메모리 덤프/VM 스냅샷은 호스트 보호 범위입니다. 유효기간12시간은 사용 차단 시각이며 만료 초에 파일을 삭제하는 예약 작업은 아닙니다. 컨테이너 재시작/중지·삭제 또는 명시적 --clear로 파일을 잃습니다. 삭제는 진행 중인 작업에 이미 전달된 키를 회수하지 않습니다.

원래 account_risk/coherent_snapshot·SDK·스냅샷·주문/보호/비상 청산·DB 저장/fencing·모델/기준·export 코드/영구 볼륨·정책·잠금 파일은 부모 ZIP과 동일합니다. 키 등록·조회·삭제는 DB/거래소를 호출하지 않습니다. 기존 복구 검토/적용의 구분·인자/토큰 검사·신규 진입 승인 규칙은 유지합니다.

## 실제 검사

집중 검사67개 통과(3.78초): 신규 키 세션 단위37개, 기존 DEMO 전송/CLI 검사28개, 실제 PostgreSQL을 쓰는 신규 키 재사용 CLI 통합2개입니다. 두 통합 검사는 등록 후 숨김 입력 호출을 실패하도록 막은 상태에서 기간 검토와 복구 검토를 실행하고 등록된 SecretStr 전달·GET만 사용·DB 불변·키 미출력을 확인합니다. 만료/역행, 권한, 잘못된 보관 장소, symlink/hardlink/FIFO/과대·손상 파일, 잘못된 입력, 키 교체 차단, 명시적 런타임 키 우선/불완전 값 차단, 일반/LIVE 컨텍스트 차단, 값 없는 상태/삭제를 검사했습니다.

실제 Linux Docker 임시 컨테이너에서 동일 tmpfs 옵션과 사용자10001:10001을 적용했습니다. 디렉터리 소유자10001:10001/권한0700, tmpfs/noexec/nodev/nosuid를 /proc/self/mountinfo로 확인했고, 합성 파일을 만든 뒤 컨테이너를 재시작해 파일이 사라짐을 확인했습니다. 실제 API 키는 이 시험에 사용하지 않았고 임시 컨테이너도 제거했습니다. docker compose config --quiet가 통과했습니다.

- 전체 pytest: 798 passed in 189.26s (0:03:09), 실제 로컬 PostgreSQL을 TEST_DATABASE_URL로 지정.
- Ruff check: All checks passed. Ruff format --check: 232 files already formatted.
- mypy: Success, no issues found in 84 source files.
- 부모 ZIP SHA-256: 9f5e9de8e3f1858bb670fea9837d2de1125e8758ad3db04b59e4f3177d2749f1. 기존273개 파일에서 CLI9개/Compose1개를 변경하고 helper·테스트2개·문서2개를 추가합니다. 변경 목록과 전 파일 SHA-256/CRC/누락을 패키징 시 검사합니다.
- main은 64e9e7b3d833824c763a2a0f2df1493300744121 그대로입니다. 별도 windows-demo-key-session-20261030 브랜치로 게시합니다.

인증된 Binance 호출이나 실제 키 저장은 클라우드에서 실행하지 않았습니다. 사용자 Windows Docker의 등록/복구 검토는 설치 후 확인할 단계입니다. 영구 키 보관/상시 서버 구축/실제 자동 매매 수락/Phase4 완료 또는 LIVE/Phase5 승인과는 별도입니다.
