# 정지된 원본 보존 패키지 — 2026-11-14

원본 API·DB·키 세션 모두 정지된 현재 Windows 구성을 위한 별도 경로입니다. 기존 정상 종료 백업 도구의 가드는 변경하지 않았습니다. 원본을 시작하거나 원본 PID를 지우지 않고 DB/WAL/PID와 런타임 전체를 읽기 전용으로 보존합니다. 키 볼륨은 제외합니다.

소스 커밋: `349831e2c1a70c9caa7ed8dedf172641eb1878e4`

ZIP SHA256: `74244ad8c1f0affaeb401b283d28c32a226aa3019e5f1ac35e5d52f88e7a39f3`

압축을 바탕화면에 푼 뒤 CMD:

```bat
cd /d "%USERPROFILE%\Desktop\windows-demo-stopped-preservation-20261114"
RUN_CHECK.cmd
```

`STOPPED_SCOPE_CHECKED_NO_SOURCE_START_NO_BACKUP_NO_ORDERS`를 확인한 경우에만:

```bat
RUN_PRESERVE.cmd
```

성공: `STOPPED_RAW_COPY_SAVED_NOT_RECOVERY_VERIFIED_NO_ORDERS`.

개인 NTFS 폴더 `%LOCALAPPDATA%\WBKANG-DEMO-Backups\cold-20261114-<UUID>`에 이미지·아카이브·해시 영수증을 저장합니다. 보조 컨테이너와 출력 볼륨은 남깁니다. 실패 시 자동 재시도·삭제·재시작을 하지 않습니다. 콘솔 JSON만 공유하고 실제 백업 내용은 업로드하지 마세요.

실제 Windows에서 백업·복구·오프라인 이관은 아직 검증되지 않았습니다. 검증 자료의 PostgreSQL 강제 종료·복제본 복구는 새로 만든 합성 시험 자원만 대상으로 합니다. Windows NTFS ACL은 Linux 시험에서 대체했으며 실제 생산 코드에는 대체 함수가 없습니다. 보존 성공은 복구 검증 성공과 다릅니다. 원본은 계속 정지 상태로 유지하세요.
