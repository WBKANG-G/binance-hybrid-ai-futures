# 날짜 변경을 구분하는 설치 후 읽기 전용 점검 V2

기존 코드·영속 저장소의 설치 완료 상태를 확인합니다. V1은 날짜가 바뀌면 `SETUP_PERIOD_MISMATCH`로 전체 점검을 중단했습니다. V2는 설치 검증과 현재 기간 일치를 구분해 보고합니다.

압축 안의 폴더를 바탕화면에 놓고 CMD에서 실행하세요.

```bat
cd /d "%USERPROFILE%\Desktop\windows-demo-runtime-readiness-review-v2-20261112"
RUN_REVIEW.cmd
```

## 기간이 다른 경우

`runtime_readiness_review`는 `LOCAL_CONTROLS_VERIFIED_PERIOD_MISMATCH_KEEP_PAUSED`, `installed_controls_verified`는 true, `period_keys_match`는 false로 표시됩니다. `periods`에 저장된 일/주와 현재 일/주가 함께 나옵니다. 설치 성공과 기간 갱신 필요를 구분한 결과이며 거래 준비 완료나 기간 갱신 완료가 아닙니다.

기간 불일치 시 거래소 API와 DEMO 키 세션에 접근하지 않고 `NOT_COLLECTED_PERIOD_MISMATCH`, HTTP 요청 0으로 보고합니다. 일/주 기준금액·고점·저장 날짜·평가 시각·이력·영수증·DB·저널을 변경하지 않습니다. 이전 무활동 확인을 오늘까지 자동 연장하지 않습니다. 자동 기간 갱신은 하지 않습니다.

다음 운영 작업은 새 기간의 근거를 별도로 확인하여 기준값 갱신 절차를 검토하는 것입니다. 이 도구는 그 적용 기능을 포함하지 않습니다. 설치 도구를 다시 실행하거나 저장소를 초기화하지 마세요.

## 기간이 일치하는 경우

`LOCAL_CONTROLS_VERIFIED_KEEP_PAUSED`로 로컬 제어 검증 결과를 보고합니다. 등록된 기존 DEMO 세션이 있으면 최대 4회의 GET으로 서버 시간·공개 한도·계정 주문 사용량 두 번을 수집합니다. 세션이 없거나 만료됐으면 키 입력을 요구하지 않습니다. 외부 조회를 아예 생략하려면 `RUN_LOCAL_REVIEW.cmd`를 사용하세요.

거래소가 주문 사용량을 생략하면 UNKNOWN을 유지합니다. `ORDER_COUNTER_NOT_PROVIDED`를 0으로 해석하지 않으며 같은 조회나 요청 제한 응답을 자동 재시도하지 않습니다. 문서 비용 계약·모델 예산 적합성은 실제 거래소 비용 상한 또는 공용 IP 예약의 증명이 아닙니다.

## 유지하는 검증

핵심 소스 55개·의존 소스 66개 해시, NORMAL·신규 진입 정지, 계정 리스 없음, 11월 11일 및 이전 영수증, 금융 원장 보존 해시, 설정 완료 바인딩, 아직 사용되지 않은 주문·공유 예산 저널을 검사합니다. 날짜가 달라도 이 검증은 생략하지 않습니다. 점검 전후 DB·저널 해시와 현재 기간을 재확인하고, 중간에 바뀌면 성공 보고서를 내지 않습니다.

재설치·컨테이너 교체·기동·저장소 생성·writer session 시작·거래 실행을 하지 않습니다. `execution_approved`, `orders_enabled`, `live_trading_enabled`, `trading_readiness_verified`는 false입니다. `execution_blockers`는 남은 운영/실행 조건입니다. 이 명령은 최신 포지션·미체결 주문 전체 조회를 대신하지 않습니다.

## 검증

이번 V2의 격리 PostgreSQL/SQLite·합성 HTTP 테스트 20개가 통과했습니다. 하루 경과·주간 변경·점검 중 날짜 경과·유효하지 않은 영수증 거부를 포함합니다. 기존 16개 검증도 모두 포함합니다. 전송 파일 검증은 PowerShell 7에서 정품 통과/변조 거부를 확인했습니다. 실제 Windows PC와 실제 거래소 응답을 클라우드에서 실행했다는 뜻은 아닙니다.

`verification` 폴더는 개발 재현용 자료입니다. 실행 CMD는 그 안의 적용/설정 코드를 호출하지 않습니다. 소스 커밋 `e03b6ba683732cd5872c30cf0d245a5b1fb6d325` checkout과 개발 의존성이 있는 환경에서 다음처럼 재현합니다.

```sh
PYTHONPATH="$PWD" TEST_DATABASE_URL=postgresql+psycopg://dev:dev@127.0.0.1:55435/hybrid pytest -q /압축해제경로/verification/runtime-readiness-review-v2-20261112/test_runtime_readiness.py
```
