# 정지 원본과 백업의 두 차례 파일 비교

이 묶음은 2026-11-24 원본 범위 확인 다음 단계입니다. 기존에 검증된 비교 helper 한 개만 재사용합니다. 원본 DB/API/key-session 및 다른 복사본은 시작하지 않습니다. 새 컨테이너나 볼륨도 만들지 않습니다.

바탕화면에 압축을 풀고 CMD에서 실행합니다.

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-source-refresh-20261124"
RUN_CHECK.cmd && RUN_COMPARE.cmd
```

API 키를 입력할 필요가 없습니다. 먼저 파일 봉인, 백업 해시, 정지 범위 및 기존 helper의 정확한 신원·읽기 전용 마운트·격리를 확인합니다. 비교 명령은 해당 helper에만 읽기 전용 Python 작업을 전달합니다. 파일 내용, 누락·추가 파일, 권한, 소유자, 수정 시각을 두 차례 비교합니다. 비공개 결과 파일은 LOCALAPPDATA의 WBKANG-DEMO-Backups 아래 새 폴더에 생성됩니다.

성공 표시는 `SOURCE_REFRESH_MATCHED_HELPER_STOPPED_NO_ORIGINAL_START_NO_ORDERS`입니다. helper가 정상 종료됐는지도 다시 확인합니다. 오류가 발생하면 재실행하지 말고 결과 화면을 전달해 주세요. 실패한 helper의 종료 상태를 덮어쓰거나 컨테이너를 삭제하지 않습니다. 시간 초과 시 Docker CLI만 종료하며 helper의 정지를 확인하지 못할 수 있습니다.

비교 성공은 현재 원본 SQL 조회나 fence 검증을 뜻하지 않습니다. 원본 쓰기·서비스 시작·소유권 인수·기준액 적용 권한은 생성하지 않습니다. 과거 미해결 요청 4건, 주문 기록, 손익 및 high-water를 보존합니다. 거래소 HTTP, 재전송, 주문 실행, 실거래는 수행하지 않습니다. 조건부 기간 재사용에 필요한 최신 계정 관측도 별도 단계입니다.
