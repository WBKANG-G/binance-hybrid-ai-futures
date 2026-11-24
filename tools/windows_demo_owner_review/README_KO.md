# 격리 복사본 소유권·fence 조회와 원본 적용 절차 초안

Docker Desktop이 준비된 상태에서 바탕화면에 압축을 풀고 CMD에서 실행합니다.

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-owner-review-20261124"
RUN_CHECK.cmd && RUN_READ_OWNER.cmd
```

API 키는 필요 없습니다. 원본 DB/API/key-session 및 이전 복사본은 시작하지 않습니다. 고정 백업을 새 볼륨으로 복원해 네트워크 없는 새 복사본 DB만 시작하며, 읽기 전용 SQL을 두 차례 실행한 뒤 복사본을 정상 종료합니다. 기존 원본 정지 범위·해시·마운트·복원 검사는 그대로 유지합니다.

SQL은 소유자 존재 여부와 해시, 임대 기한·상태, fence를 읽습니다. 소유자 원문은 출력하지 않습니다. 현재 SQL row lock을 획득하거나 소유자를 해제하지 않습니다. 복사본에서 확인한 값은 현재 원본 SQL·잠금 검증을 대신하지 않습니다.

11월 24일/주차 23일의 4998.74183608 USDT 후보와 전체 JSONB 보존 차이도 조회만 합니다. `OWNER_PROTOCOL_DRAFT.json`과 `OWNER_REVIEW_RECEIPT.json`은 LOCALAPPDATA의 WBKANG-DEMO-Backups 아래 새 비공개 owner-review 폴더에 저장됩니다. 함께 제공한 ORIGINAL_RECOVERY_PROTOCOL_KO.md는 향후 원본 복구 절차 초안이며 실행 명령이나 원본 쓰기 프로그램이 아닙니다.

성공 표시는 `OWNER_REVIEW_COMPLETE_COPY_STOPPED_NO_ORIGINAL_START_NO_APPLY_NO_ORDERS`입니다. 만료된 소유자라도 자동 인수하지 않습니다. 원본 시작·쓰기, 기준액 적용, 과거 요청 해소·재전송, 주문 실행 권한은 생성하지 않습니다. 이번 묶음은 거래소에 접속하지 않으며, 앞선 성공 관측을 현재 시점의 최신성 증명으로 재사용하지 않습니다.

오류가 나오면 재실행·삭제·강제 종료하지 말고 결과 화면을 보내세요. 기존 원본과 이전 복사본은 계속 정지 상태로 유지하세요.
