# 현재 Windows 런타임의 읽기 전용 현황 진단

이 패키지는 현재 컨테이너의 코드 해시·후보 테이블 존재 여부·대상 계정의 제한된 상태 집계를 읽습니다. 설치·이미지 빌드·컨테이너 재생성·스키마 생성·일시정지 변경·키 세션 등록·공유 저널 열기·시계 복구·참조 생성·외부 보관·거래소 HTTP·주문을 하지 않습니다.

사용자가 선택한 보관 방식은 별도 기기·오프라인입니다. 이 진단은 그 기기를 설정하거나 파일을 반출하지 않습니다. 기존 Windows에 새 후보 모듈·테이블이 없으면 현황으로 표시하며 자동 설치나 재시도를 하지 않습니다.

다운로드한 ZIP을 바탕화면에 압축 해제한 뒤 CMD에서 한 번 실행합니다.

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-runtime-inventory-20261113"
RUN_INVENTORY.cmd
```

이 작업에는 DEMO API key/secret 입력이 필요하지 않습니다. 대상은 hybrid-windows-api-1, 작업 디렉터리 /app, 컨테이너 사용자 10001, compose project/service 라벨 hybrid-windows/api입니다. 일치하지 않거나 컨테이너가 실행 중이 아니면 중단합니다.

전송 해시를 확인한 다음 검증한 코드를 Python -I -B의 표준입력으로 실행합니다. 코드를 /app에 복사·압축 해제하거나 pyc를 생성하지 않습니다. docker inspect에서는 필요한 개별 필드만 읽고 Config.Env 전체를 가져오거나 출력하지 않습니다. DB의 DATABASE_URL 연결 설정은 프로세스 안에서만 사용하며 값은 출력하지 않습니다. 거래소 키·private RAM 세션 파일은 읽지 않습니다.

DB는 READ ONLY/REPEATABLE READ로 읽고 각 SQL에 statement/lock timeout 2초를 적용합니다. demo_accounts 전체 원장이나 과거 이벤트 본문을 가져오지 않습니다. 코드 파일은 고정된 Python 경로만 해시하며, 계정 정보는 paused/risk/기간 키/주문 개수/허용한 요청 상태별 개수만 출력합니다. 새로운 후보 테이블은 이름의 존재만 확인합니다. 트리거·데이터 무결성이나 적용 완료를 인증하지 않습니다.

출력에서 확인할 내용:

- baseline_files_verified: 마지막 확인된 읽기 코드 설치 소스와 같은 Python 파일 개수.
- changed_or_missing_baseline_files: 누락 또는 다른 코드 파일. 파일 원문·키는 출력하지 않습니다.
- changed_or_missing_candidate_files: 현재 검토 후보와 같지 않거나 없는 파일. 자동 교체하지 않습니다.
- table_presence: 기존/새 감사 테이블 이름의 존재 여부.
- paused/risk_state/기간 키/period_keys_match: 현재 읽기 시점의 상태. 기간이 다르더라도 자동 변경하지 않습니다.
- request_state_counts/local_orders: 원래 ID 없이 집계한 로컬 이력 현황. 원격 결과나 현재 거래소 상태를 뜻하지 않습니다.

READ_ONLY_INVENTORY_COMPLETE_KEEP_PAUSED는 진단 질의가 끝났다는 뜻입니다. 새 후보 적용 완료나 거래 준비 완료가 아닙니다. 종료 2/BLOCKED이면 출력만 공유하고 자동으로 재실행·복구·주문 재시도를 하지 않습니다. SQLSTATE가 있는 경우 원시 오류 대신 코드만 표시합니다. 키·서명·DB URL·SQL·파라미터·원장/파일 내용·원시 Docker 출력은 표시하지 않습니다.

실행 후 TRANSFER_FILES_VERIFIED부터 마지막 JSON/완료 표시까지의 출력을 공유해 주세요. 이 결과로 실제 설치와 새 후보 경로 사이의 차이를 검토합니다. 출력이 실패하더라도 Docker 재설치·컨테이너 재시작·과거 RUN_APPLY 재실행은 이 패키지의 다음 동작이 아닙니다.

PowerShell 5.1용 문법으로 작성했지만 클라우드에서 직접 검증할 수 있는 것은 Linux PowerShell 7·Docker입니다. 실제 Windows CMD/PowerShell 5.1 결과는 사용자 실행으로 확인해야 합니다. 소스 참조·ZIP SHA-256·합성 검증 결과는 함께 게시한 artifact 검토 문서에서 확인합니다.
