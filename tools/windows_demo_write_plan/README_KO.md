# 원본 조회 증거를 연결한 미승인 기간 변경안

바탕화면에 압축을 풀고 CMD에서 실행한다. API 키와 Docker는 필요 없다.

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-write-plan-20261124"
RUN_CHECK.cmd && RUN_PREPARE.cmd
```

이 도구는 성공한 원본 조회의 비공개 JSON 세 개를 읽고 해시를 고정해 미승인 변경안만 만든다. 원본 DB를 다시 열거나 SQL을 실행하지 않는다. 승인받아 사용한 원본 조회 시작 권한을 재사용하지 않는다. 원본 서비스와 이전 임시 DB는 정지 상태로 유지한다.

후보는 11월 24일/주차 23일, 기준액 4998.74183608 USDT다. 원장에서는 기간 키와 새로운 감사 항목 하나, 메타데이터에서는 fence 110→111을 한 트랜잭션으로 처리하는 안이다. 소유자 없음 및 기존 만료 임대 기한은 그대로 보존하며 소유권 정리·인수는 하지 않는다. 초기값·high-water 5000, 손익 구성과 평가 시각, 34개 주문, 미해결 요청 4건을 포함한 전체 요청·기존 operations·나머지 JSONB도 보존한다. 실제 변경은 없다.

UNAPPROVED_WRITE_PLAN.json은 LOCALAPPDATA의 WBKANG-DEMO-WritePlans 아래 새 비공개 폴더에 저장된다. 성공 표시는 `UNAPPROVED_WRITE_PLAN_READY_NO_ORIGINAL_START_NO_APPLY_NO_ORDERS`다. 결과 화면을 전달한다. 오류가 나면 원본 파일이나 이전 보고서를 고치거나 지우지 말고 그대로 전달한다.

검토용 transaction_candidate.py는 격리 복사본에서만 검증한 트랜잭션 엔진 후보다. 원본 쓰기 진입점은 연결을 열기 전에 항상 차단돼 있다. Windows 실행 명령은 이 Python 파일을 실행하지 않는다. Copy 기록·합성 freshness 증거는 원본 적용 승인이나 실제 최신 거래소 증거가 아니다.

원본 시작 이후 저장소 파일은 이전 백업과 달라질 수 있다. 실제 쓰기 전에 새 백업·정지 범위/임시 서버의 정확한 신원 검토, 최신 계정 소스와 양쪽 7일 간격, 원본 row/advisory lock·상태/fence/소유권 CAS, 실제 원본용 host adapter 및 최종 payload 검증·별도 쓰기 승인이 필요하다. 현재 변경안의 UUID는 작업 식별자이며 apply 토큰이 아니다. 이번에는 데이터베이스·네트워크·주문에 접근하지 않는다.
