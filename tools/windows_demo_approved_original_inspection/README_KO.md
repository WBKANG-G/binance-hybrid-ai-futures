# 승인된 원본 DB 1회 시작·읽기 전용 조회·정상 종료

앞서 제시한 한정 질문에 대한 사용자 답변 `다음 단계 진행해줘`를 원문 그대로 별도 승인 기록에 연결했다. 정확한 메시지 시각은 알 수 없으며 임의의 시각을 만들지 않았다. 기존 소스 승인 기록과 검토 proposal은 수정하지 않았다. 검토받은 실행 코드·SQL·가드는 같은 바이트를 사용한다.

Docker Desktop이 준비된 상태에서 바탕화면에 압축을 풀고 CMD에서 한 번 실행한다.

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-original-inspection-20261124"
RUN_CHECK.cmd && RUN_INSPECT.cmd
```

API 키는 필요 없다. 실행 중 Docker를 재시작하거나 원본 DB/API/key-session·이전 복사본을 별도로 시작하지 않는다.

RUN_CHECK는 정지 범위·백업을 점검만 하며 단일 시도를 소비하지 않는다. 점검 끝의 NO_APPROVAL_GRANTED 표시는 점검 명령 자체가 새 승인을 부여하지 않는다는 뜻이다. 이번 한정 시작 승인은 별도 ORIGINAL_START_ACCEPTANCE.json에 기록돼 있다. INSPECTION_PROPOSAL.json은 검토 당시의 문서이므로 승인 전 상태 그대로 보존했다.

RUN_INSPECT는 승인·정지 범위·고정 백업을 확인하고 기존 읽기 전용 helper로 원본 DB와 런타임 파일을 두 차례 비교한다. 승인별 비공개 결과 디렉터리를 단일 시도 표식으로 만든다. 이후 원본 DB 볼륨만 연결한 임시 PostgreSQL 한 개를 network=none, TCP listen 없음, Unix socket, SQL 기본 read-only로 시작한다. 고정된 REPEATABLE READ READ ONLY 조회 두 번 후 SIGINT 정상 종료를 확인한다. 원본 서비스 컨테이너는 시작하지 않는다. 새 볼륨·원본 런타임 마운트도 없다.

**PostgreSQL 시작·종료 중 원본 WAL·복구·제어 파일 기록은 발생할 수 있다.** 승인 범위는 이 저장소 변화까지다. 원장 UPDATE, owner/lease 변경, fence 증가, 기준액 적용, 과거 요청 해소·재전송, 거래소 HTTP·주문·실거래는 허용하거나 실행하지 않는다. 현재 원본을 조회하더라도 원본 적용 준비나 거래 재개 승인이 되지 않는다.

성공 표시는 `ORIGINAL_INSPECTION_COMPLETE_TEMP_SERVER_STOPPED_NO_LEDGER_WRITE_NO_ORDERS`다. 결과는 LOCALAPPDATA의 WBKANG-DEMO-Backups 아래 승인별 original-inspection 폴더에 ORIGINAL_INSPECTION_RECEIPT.json으로 저장된다. 임시 컨테이너는 종료한 상태로 보존한다.

오류 또는 정상 종료 미확인 시 자동 재시도·강제 종료·PID 직접 삭제·리소스 삭제·원본 백업 덮어쓰기를 하지 않는다. **같은 명령을 다시 실행하지 말고 결과 화면을 보내 달라.** 준비 대기 루프 60초, 종료 관측 루프 30초 및 개별 Docker 명령 제한이 있어 전체 소요 시간은 더 길 수 있다. 정상 종료 확인 실패는 성공으로 표시하지 않는다. 시작 이후 원본 물리 파일은 예전 백업과 달라질 수 있으므로 이후 비교 가드를 우회하지 않는다.
