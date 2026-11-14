# 정지된 원본 보존 — 2026-11-14

원본 API·DB·키 세션을 시작하지 않습니다. 프로젝트 7개 컨테이너가 모두 정지되고 재시작 정책이 no이며, 검토한 원본 ID·볼륨 관계가 일치해야 실행됩니다. 외부 볼륨 소비자가 있으면 차단합니다. 원본 DB 볼륨 전체(WAL·PID 포함), 런타임 볼륨 전체, API/DB 이미지를 보존합니다. 키 세션 및 /run/demo-keys는 복사하지 않습니다. 원본 환경 변수·API 키를 읽지 않습니다.

압축을 바탕화면에 풀고 CMD에서 실행합니다.

```
cd /d "%USERPROFILE%\Desktop\windows-demo-stopped-preservation-20261114"
RUN_CHECK.cmd
```

STOPPED_SCOPE_CHECKED_NO_SOURCE_START_NO_BACKUP_NO_ORDERS가 나오면 같은 CMD에서 다음을 실행합니다.

```
RUN_PRESERVE.cmd
```

실제 보존은 Windows NTFS의 `%LOCALAPPDATA%\WBKANG-DEMO-Backups\cold-20261114-<UUID>`에 현재 사용자와 SYSTEM만 접근하도록 저장합니다. Docker에 읽기 전용 원본 마운트·네트워크 없음·자동 재시작 없음인 보존 컨테이너와 별도 출력 볼륨을 생성합니다. 원본 start/stop/update/rm 명령은 없습니다. 생성한 보조 컨테이너와 출력 볼륨은 보존되며 자동 삭제·재시도·원본 재시작을 하지 않습니다.

성공 표시는 `STOPPED_RAW_COPY_SAVED_NOT_RECOVERY_VERIFIED_NO_ORDERS`입니다. 로컬 파일 길이·SHA256 검증 성공이며 정상 종료나 복구 가능성의 증거는 아닙니다. 원본 DB는 시작하지 않아 paused 상태·기간·계좌 내용을 새로 검증하지 않습니다. 오래된 PID 파일도 지우지 않습니다. 사본 복구 검증, 별도 기기 오프라인 이관은 아직 하지 않습니다.

실패하면 재실행하지 말고 콘솔 JSON만 공유하세요. 실제 백업 파일, BACKUP_STATE.json, 볼륨 내용, 키를 업로드하지 마세요. 남은 출력 폴더·보조 컨테이너·볼륨을 보존하고 원본은 계속 정지시킵니다. Docker Desktop을 종료·재시작하거나 컨테이너를 수동 시작하지 마세요.

파일·링크·확장 메타데이터·용량·시간 제한은 기존 보존 라이브러리와 같습니다. 심볼릭 링크/하드링크/특수 파일/확장 속성 발견 시 묵살하지 않고 차단합니다. 호스트 최소 여유 공간 2GiB, 이미지 및 아카이브 별도 여유 공간도 검사합니다. Windows NTFS ACL은 Linux 시험에서 대체 함수로만 시험되었으며 실제 Windows 실행은 결과를 확인해야 합니다.
