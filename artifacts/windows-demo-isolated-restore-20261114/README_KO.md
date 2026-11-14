# 보존 사본 격리 복구 검사 — 2026-11-14

원본 API·DB·키 세션을 계속 정지시킨 채, 사용자 콘솔에서 확인된 보존 사본만 새 UUID 볼륨에 복원합니다. 원본 볼륨은 복구 작업자/PG 사본에 마운트하지 않습니다. 보존 파일 6개는 읽기 공유로만 열어 해시 검사 전후 동안 유지합니다.

소스 커밋: `01d64cbf901ad7cb94ee77dedeb49808c1025b4a`

ZIP SHA256: `23af639f252d513dc3a2002eb313fe67276f31a906afe8cbc691afbebefad16b`

사용자가 보내준 두 해시:

- manifest: `2a2af624a12c696868ec10ce2522a97b29c2824fe6571dc232840f22db3bdd9d`
- 영수증: `4aff6c186597d9ea97a12928101961f5143c93dfae36b5b647109d11676b6778`

압축을 바탕화면에 풀고 CMD:

```bat
cd /d "%USERPROFILE%\Desktop\windows-demo-isolated-restore-20261114"
RUN_CHECK.cmd
```

`RESTORE_CHECK_PASSED_NO_CLONE_NO_SOURCE_START_NO_ORDERS` 확인 후에만:

```bat
RUN_RESTORE.cmd
```

성공 문구: `ISOLATED_COPY_RECOVERED_READ_CHECKED_STOPPED_NO_ORDERS`.

사본 DB의 오래된 PID만 검증 후 제거하고, PG18을 네트워크 없음·외부 복구 명령 및 사전 로드 라이브러리 비활성화·READ ONLY 거래로 검사합니다. 사용한 UID는 root가 아니며 PG 데이터 디렉터리의 정상적인 GID 0도 지원합니다. 사본의 주문 34개·미해결 요청 4개·저장 일자 2026-11-13 및 주 2026-11-09를 확인하며 계좌 state 해시가 두 번의 조회에서 일치해야 합니다. 현재 날짜 채택/계좌 수정/과거 요청 해소/거래소 요청은 없습니다.

검사 후 사본도 SIGINT로 정지하여 exit=0, OOM=false를 확인하고, 원본 상태와 입력 파일 해시를 재검증합니다. 보존 폴더는 수정하지 않습니다. 별도 개인 NTFS 보고서 폴더에 검사 영수증을 저장하며 복제 볼륨·컨테이너는 남깁니다. 실패 시 원본을 시작하거나 재실행하지 말고 콘솔 JSON만 공유하세요. 실제 백업 파일은 업로드하지 마세요.

실제 Windows 실행·NTFS ACL·Docker Desktop 호스트 폴더 읽기 공유 마운트는 아직 검증하지 않았습니다. 합성 시험은 새 PostgreSQL을 강제 종료한 뒤 사본에서만 복구했고 원본 파일 해시도 유지되는지 검사했습니다. 실제 보존 사본의 복구 성공은 사용자 PC 실행 결과가 필요합니다. 전체 재무 증명, 원격 거래소 상태, 설치/거래 준비, 오프라인 이관은 별도 단계입니다.
