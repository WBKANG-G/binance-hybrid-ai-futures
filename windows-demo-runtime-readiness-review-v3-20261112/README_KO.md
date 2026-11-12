# 설치 후 읽기 전용 점검 V3: 실패 단계와 안전한 오류 코드

V2의 REVIEW_UNAVAILABLE만으로는 실제 중단 원인을 구분할 수 없었습니다. V3는 동일한 검증을 유지하며 stage, stop_reason, failure_category를 구분합니다. 설치를 다시 시도하거나 실패한 조건을 우회하지 않습니다.

압축 안 폴더를 바탕화면에 놓고 CMD에서 실행하세요.

```bat
cd /d "%USERPROFILE%\Desktop\windows-demo-runtime-readiness-review-v3-20261112"
RUN_REVIEW.cmd
```

출력 마지막 JSON을 공유하면 다음 조치를 구체적으로 정할 수 있습니다. 비밀값·SQL·파일 경로·네트워크 응답·예외 원문은 출력하지 않습니다. 키를 채팅에 입력하지 마세요.

예시: stage=SHARED_JOURNAL_VALIDATION, stop_reason=SHARED_CLOCK_OR_BOOT_CHANGED이면 공유 저널의 부팅/시계 검증에서 중단됐다는 뜻입니다. 이 예시는 사용자 PC에서 그 오류가 발생했다고 확인한 결과가 아닙니다. 자동 저널 초기화·새 부팅 식별자 채택·설정 영수증 재생성은 하지 않습니다.

DB 읽기 실패는 REVIEW_DATABASE_READ_FAILED, 파일 권한 문제는 REVIEW_FILESYSTEM_PERMISSION_DENIED, 알려진 제어 거부는 해당 고정 코드를 표시합니다. 알 수 없는 예외는 안전한 분류와 단계만 표시합니다. 모든 엔진은 종료 시 정리합니다.

기간이 다른 경우 V2와 동일하게 설치 상태와 기간 불일치를 구분해 표시하며 거래소와 키 세션에 접근하지 않습니다. 저장 날짜와 현재 날짜를 함께 표시하고 기존 무활동 확인을 자동 연장하지 않습니다. 기간이 같고 기존 DEMO 세션이 있으면 최대 4회의 GET으로 주문 사용량 증거를 수집합니다. 사용량 누락은 UNKNOWN이며 0으로 채우지 않습니다. 조회 제한에 재시도하지 않습니다. 외부 조회를 생략하려면 RUN_LOCAL_REVIEW.cmd를 실행합니다.

소스 55개·제어 의존 파일 66개 해시, NORMAL·정지 상태, 리스 없음, 기존 청산 및 기간 영수증, 설정·바인딩, 빈 저널, 점검 전후 DB/파일 해시를 검사합니다. 이번 도구는 DB/저널/기준금액/저장 날짜/이력/설치 상태를 변경하지 않습니다. 컨테이너를 기동하거나 교체하지 않습니다. 실행 승인과 주문·실거래는 계속 false입니다.

이번 테스트 28개 통과: 기존 20개와 CLI 오류 분류·예외 비밀정보 제거·엔진 정리·실제 공유 저널 부팅 불일치 거부 검증을 포함합니다. 네트워크 없는 Docker에서 DB 연결 불가를 DATABASE_SNAPSHOT_BEFORE 단계의 구체적인 코드로 보고하는 것도 확인했습니다. 실제 사용자 PC의 원인이 해결됐다는 뜻은 아니며 이번 출력으로 원인을 확인해야 합니다.

verification 폴더는 재현용이며 CMD에서 적용/설정 코드나 테스트를 실행하지 않습니다. 소스 기준 커밋 e03b6ba683732cd5872c30cf0d245a5b1fb6d325 및 개발 의존성을 갖춘 checkout에서 재현합니다.

```sh
PYTHONPATH="$PWD" TEST_DATABASE_URL=postgresql+psycopg://dev:dev@127.0.0.1:55435/hybrid pytest -q /압축해제경로/verification/runtime-readiness-review-v3-20261112/test_runtime_readiness.py
```
