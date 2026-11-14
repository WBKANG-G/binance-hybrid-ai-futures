# 보존 사본의 격리 복구 검사 — 2026-11-14

사용자가 보내준 manifest/영수증 SHA256에 정확히 일치하는 보존 폴더 하나를 자동 선택합니다. 백업 파일 6개를 읽기 공유로만 열어 검사하는 동안 수정 접근을 막고, 영수증·state·이미지·두 아카이브·manifest의 해시를 확인합니다. 원본 API/DB/키 세션은 시작하지 않습니다. 키·환경 변수를 읽지 않습니다.

바탕화면에 압축을 풀고 CMD:

```bat
cd /d "%USERPROFILE%\Desktop\windows-demo-isolated-restore-20261114"
RUN_CHECK.cmd
```

`RESTORE_CHECK_PASSED_NO_CLONE_NO_SOURCE_START_NO_ORDERS`가 나온 경우에만:

```bat
RUN_RESTORE.cmd
```

새 UUID 볼륨 두 개에만 DB와 런타임을 복원합니다. 전체 아카이브 해시·헤더 검증을 먼저 하고, 파일 바이트·소유자·권한·수정 시간을 복원합니다. 오래된 postmaster.pid가 있으면 보존된 해시를 확인한 뒤 **복제 볼륨의 PID 파일만** 지웁니다. 원본 볼륨은 복구 컨테이너에 마운트하지 않습니다.

원본과 같은 PG 이미지를 로컬 캐시에서만 사용해 복제 DB를 `network=none`, 포트 공개 없음, 자동 재시작 없음, 읽기 전용 루트로 실행합니다. WAL 복구는 복제 볼륨에만 씁니다. 원본 설정의 외부 복구 명령·사전 로드 라이브러리는 실행 옵션으로 차단합니다. 고정된 dev/hybrid 로컬 소켓으로 READ ONLY 거래를 두 번 수행해 paused=true, 주문 34개·미해결 요청 4개·저장 날짜 2026-11-13·주 2026-11-09와 계좌 state 해시 일치를 검사합니다. 현재 날짜를 채택하거나 과거 요청을 해결하지 않습니다.

완료 시 복제 DB도 SIGINT로 정지하고 exit=0/OOM=false를 확인합니다. 보존 파일 해시와 원본 컨테이너의 정지·설정 상태를 재확인합니다. 성공 문구: `ISOLATED_COPY_RECOVERED_READ_CHECKED_STOPPED_NO_ORDERS`.

검사 영수증은 별도 NTFS 개인 폴더 `%LOCALAPPDATA%\WBKANG-DEMO-Backups\cold-20261114-<새 UUID>`에 저장합니다. 원래 보존 폴더에는 쓰지 않습니다. 복제 볼륨과 보조 컨테이너는 남깁니다. 실패하면 생성된 복제 DB만 정지 시도하며 SIGKILL·자동 삭제·재시도는 없습니다. 콘솔 JSON만 공유하고 백업/계좌 내용은 업로드하지 마세요. 원본을 계속 정지시키고 이 명령을 재실행하지 마세요.

이 검사는 로컬 사본의 복원 및 계좌 읽기 검증입니다. 원격 거래소 상태, 전체 재무 증명, 실거래 준비, 별도 기기 오프라인 이관은 검증하지 않습니다. 실제 Windows NTFS ACL과 Docker Desktop의 호스트 폴더 읽기 공유 마운트는 실제 PC 실행 결과가 필요합니다. 합성 Linux 시험 결과를 실제 PC의 복구 성공으로 표시하지 않습니다.
