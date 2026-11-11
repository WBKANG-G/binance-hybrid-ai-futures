# TP 제어 코드와 로컬 저장소 배포 점검

11월 11일 일간 적용과 READ_ONLY_READY 확인 이후 다음 설치 작업을 결정하는 조회 도구입니다. 같은 거래소 한도 조회를 반복하지 않습니다. 거래소 HTTP 요청, 키 조회, DB 쓰기, 리스 획득, 저장소 생성, 컨테이너 재빌드, 거래 재개를 하지 않습니다.

ZIP 안의 `windows-demo-runtime-deployment-review-20261111` 폴더를 바탕화면에 두고 CMD에서 실행하세요.

```bat
cd /d "%USERPROFILE%\Desktop\windows-demo-runtime-deployment-review-20261111"
RUN_REVIEW.cmd
```

현재 설치된 핵심 소스 55개 해시와 11월 11일 일간 적용 영수증을 확인합니다. PostgreSQL REPEATABLE READ / READ ONLY 트랜잭션에서 계정·위험 상태·일간/주간 날짜를 읽고, 공유 예산/주문 한도 제어의 핵심 파일 11개를 최신 검증 소스와 비교합니다. 영속 주문 한도 및 공유 예산 저장소의 DB 등록 여부도 확인합니다.

출력에서 다음을 구분합니다.

- `candidate_control_files.missing`: 아직 없는 제어 파일
- `candidate_control_files.different`: 검증 버전과 다른 파일
- `durable_order_storage`, `shared_weight_storage`: 저장소 등록 없음 / 잘못된 등록 / 등록은 있으나 파일 미검증
- `next_action`: 코드 설치 검토를 먼저 할지, 저장소 설정 검토를 먼저 할지

`LOCAL_DEPLOYMENT_REVIEW_COMPLETED_KEEP_PAUSED`는 로컬 점검 완료입니다. 거래소 준비 완료나 TP 실행 승인이 아닙니다. 11개 파일 비교는 전체 의존성 검증이 아니며, DB에 저장소 등록이 있어도 실제 저장소 파일이 유효하다고 주장하지 않습니다. 실제 계정 주문 사용량과 외부 IP 여유는 이 도구가 확인하지 않습니다.

출력 JSON을 공유해 주세요. 날짜가 바뀌었거나 중단 코드가 나오면 그대로 공유하고 기준값을 다시 적용하거나 거래를 재개하지 마세요. 키가 필요하지 않아 DEMO 세션 만료 여부와 관계없이 로컬 점검할 수 있습니다.

검증: 합성 거래소로 만든 적용 완료 원장과 격리 PostgreSQL에 대한 21개 검사, Ruff 및 Docker 네트워크 차단 검사 통과. 실제 DB 트랜잭션 읽기 전용 상태, 계정/이벤트/outbox 불변, 키/네트워크 미사용, 오류 비노출을 검사했습니다. 실제 Windows에서의 결과는 실행 후 확인합니다. 이전 1,872개 회귀 검사나 실제 거래소 실행을 이번 결과로 계산하지 않았습니다.

테스트 재현에는 Linux의 프로젝트 테스트 환경과 격리 PostgreSQL(TEST_DATABASE_URL)이 필요합니다. 프로젝트 루트에서 `pytest evidence/runtime-deployment-preflight-20261111/test_runtime_deployment.py`를 실행할 수 있으며, 파일 경로는 압축을 푼 위치에 맞춰 지정합니다. evidence/ 안의 적용 도구는 합성 테스트 원장 준비용 소스이며 RUN_REVIEW.cmd는 이를 실행하거나 가져오지 않습니다.
