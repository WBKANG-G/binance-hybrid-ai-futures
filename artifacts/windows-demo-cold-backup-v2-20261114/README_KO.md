# Windows 콜드 백업 v2 — 2026-11-14 KST

먼저 **조회 전용 `RUN_CHECK.cmd`**로 내부 계획 결과를 확인합니다. 이 명령은 서비스 정지·이미지 저장·백업 폴더 생성·백업 생성을 하지 않습니다. 계정 pause/lease 판독을 위한 READ ONLY SQL은 수행합니다.

`RUN_BACKUP.cmd`는 실제 실행 명령입니다. API를 정상 정지한 뒤 PostgreSQL DB를 정상 종료하고 백업합니다. 완료·실패 후 원본 서비스를 자동 재시작하지 않습니다. 키 세션은 유지하며, 실제 백업 실행 중 앱 조회도 중단됩니다.

바탕화면에 압축을 풀고 CMD에서 실행하세요. 키 세션 등록은 필요 없습니다.

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-cold-backup-v2-20261114"
RUN_CHECK.cmd
```

조회 성공 문구: `COLD_BACKUP_CHECK_PASSED_NO_STOP_NO_BACKUP_NO_ORDERS`

실제 백업 성공 문구: `COLD_BACKUP_SAVED_LOCAL_VERIFIED_KEEP_STOPPED_NO_ORDERS`

콘솔 JSON과 종료 문구만 공유하세요. 실제 백업 tar·manifest·상태 파일·영수증은 채팅/GitHub에 올리지 마세요. 실패하면 자동 재실행하거나 원본 컨테이너를 임의로 시작하지 말고 결과를 검토하세요. 부분 파일과 Docker 백업 볼륨·helper는 보존합니다.

## 저장 위치와 내용

`%LOCALAPPDATA%\WBKANG-DEMO-Backups\cold-20261114-<UUID>`에 새 폴더를 만들고 현재 사용자와 SYSTEM만 허용하는 NTFS ACL을 적용·검증합니다. reparse point, 지원 외 파일시스템, 디스크 부족, 권한 검증 실패는 정지 전에 차단합니다. Linux PowerShell에서는 이 Windows ACL을 실제 적용할 수 없으므로 실제 Windows 결과를 확인해야 합니다.

- `images.tar`: 원본 API·PG 이미지의 오프라인 복구용 복사본
- `database.tar`, `runtime.tar`: 정지된 PG 데이터 볼륨 전체와 런타임 볼륨 전체
- `manifest.json`: 파일별 해시·UID/GID·mode·mtime와 tar 해시
- `BACKUP_STATE.json`: 이미지·볼륨·중단 전 컨테이너 구성 메타데이터와 계정 pause/lease 판독 결과
- `BACKUP_RECEIPT.json`: 로컬 복사·해시 검증 결과와 원본 정지 상태
- 중단 시도 기록 및 실패 시 가능한 범위의 `FAILURE.json`

키 세션 tmpfs `/run/demo-keys`, 컨테이너 런타임 환경변수 전체, API 키·Secret·서명은 복사 대상으로 읽지 않습니다. PG 데이터에는 역할/인증 설정·다른 DB·계정 이력이 포함될 수 있으므로 이 폴더를 보호된 로컬 백업으로 취급하세요. 별도 기기·오프라인 인계는 아직 실행하지 않습니다.

## 실행 조건과 순서

1. v2 읽기 전용 계획을 새로 실행합니다. 확인된 원본 API/DB ID, 대상 6개와 역할 수, 실행 중인 API/DB 각 1개, 기존 runtime 소비자 3개, 키 세션 1개, 전체 재시작 정책 `no` 조건을 유지합니다. 다른 컨테이너/정책/볼륨 소비자가 발견되면 차단합니다. 정책을 자동 변경하지 않습니다.
2. 보호된 저장소·상호 배제 mutex를 준비하고 이미지를 ID로 저장합니다. source image pull은 하지 않습니다.
3. API 안에서 고정 계정의 paused=true, active lease=false, 주문 34개·미해결 요청 4개를 READ ONLY SQL로 재확인하고 원본 토폴로지를 다시 대조합니다. 11월 14일에 실행해도 저장된 기간 키·잔고 기준은 변경하지 않습니다.
4. 실행 중인 writer에 SIGTERM, PG에 SIGINT(빠른 정상 종료)를 보내고 각각 최대 30/60초 동안 정지를 관측합니다. **SIGKILL을 보내지 않습니다.** 지연·실패 시 API만 정지됐거나 DB가 계속 실행 중일 수 있으므로 결과의 `source_state_may_have_changed`를 확인하세요.
5. DB exit code 0·OOM 아님과 `pg_controldata`의 `shut down` 상태를 확인합니다. 원본 볼륨을 읽기 전용으로만 마운트한 네트워크 없는 helper가 두 tar와 manifest를 생성합니다. PG_VERSION과 postmaster PID 부재도 검사합니다.
6. Docker cp로 binary 파일을 보호된 폴더에 복사하고 Flush(true), 파일 ACL, tar·이미지·manifest 해시를 확인합니다. 원본/재시작 정책/컨테이너 집합도 다시 확인합니다. 원본을 재시작하거나 요청을 재실행하지 않습니다.

## 제한

archive는 소스 링크·특수 파일·파일 hard link·ACL/xattr 등 지원하지 않는 확장 메타데이터를 거부합니다. 그룹별 최대 50,000개 항목·파일 하나 2GiB·논리 데이터 8GiB·깊이 32·생성 예산 300초이며 자동으로 한도를 늘리지 않습니다. 새 파일/폴더를 덮어쓰거나 실패한 백업을 자동 재개하지 않습니다.

호스트 디스크와 Docker 출력 볼륨의 여유 공간을 검사하지만 Docker/Windows의 전체 디스크 내구성·특권 사용자의 동시 조작까지 보장하지 않습니다. 확인하는 quiescence는 Docker 컨테이너/볼륨 소비자의 관측 범위입니다. source DB 정상 종료 과정의 WAL/체크포인트 쓰기는 발생하며, 실행기가 주문·재무 기간·잔고 기준을 변경하는 SQL을 수행하지는 않습니다.

성공은 로컬 백업 생성·해시 검증까지입니다. 실제 Windows에서의 격리 복원 시험, 외부 오프라인 인계, 외부 최신 해시 보관, 설치·거래 준비는 완료된 것으로 표시하지 않습니다. 다음 단계에서 원본을 덮어쓰지 않는 복제 볼륨으로 복원을 검증합니다.

## v2 내부 계획 전달 수정

내부 계획 검사를 별도 powershell.exe의 출력 3줄로 판정하던 방식을 제거했습니다. 해시가 고정된 동일 계획 스크립트를 같은 프로세스에서 data-only 모드로 실행합니다. 단독 계획 명령의 기본 출력과 경로·버전·볼륨·정지 조건은 유지합니다. `fresh_plan`에는 허용된 단계·차단 코드·PG probe 상태만 표시하며 원문/예외/비밀값을 출력하지 않습니다. 화면의 기존 `COLD_FRESH_PLAN_BLOCKED`만으로 실제 Windows 내부 원인을 확정할 수는 없습니다. 우선 RUN_CHECK 결과를 확인하세요. 조회 모드는 NTFS ACL이나 실제 백업 준비 완료를 증명하지 않습니다.

## 배포 검증

소스 커밋: `b3b37641f4075fe1b27103a7a140a5e74eb584d6`

ZIP SHA256: `f7102cb4b7112d092099f677b70362f4c82a011a5d5ae8b6928710de9bc6514e`

회귀 검사 116개 및 최종 ZIP 검사 69개 통과(두 집합 중복). 실제 Docker에서 조회 모드의 무중단과 백업·새 볼륨 복원을 검증했습니다. Windows NTFS ACL 함수는 Linux 시험에서 대체했으며 실제 Windows 실행은 아직 확인 전입니다.

[수정 범위와 제한](https://github.com/WBKANG-G/binance-hybrid-ai-futures/blob/b3b37641f4075fe1b27103a7a140a5e74eb584d6/docs/reviews/20261114/COLD_BACKUP_V2_PLAN_TRANSPORT_KO.md)
