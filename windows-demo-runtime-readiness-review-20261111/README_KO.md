# 설치 완료 후 읽기 전용 런타임 점검

기존 코드·영속 저장소 설치가 완료된 2026-11-11 DEMO 계정을 위한 후속 점검입니다. 재설치나 컨테이너 교체가 없습니다.

ZIP 안의 폴더를 바탕화면에 놓고 CMD에서 실행합니다.

```bat
cd /d "%USERPROFILE%\Desktop\windows-demo-runtime-readiness-review-20261111"
RUN_REVIEW.cmd
```

## 확인하는 내용

- 설치된 핵심 소스 55개와 제어 코드 의존 파일 66개의 해시
- NORMAL·신규 진입 정지, 현재 일간/주간 기간 일치, 11월 11일 적용 영수증
- 영속 저장소 바인딩·설정 완료 영수증·시작 대기 완료 및 아직 사용되지 않은 주문/공유 예산 저널
- 금융 원장과 저장소 파일이 점검 전후 동일함
- 문서 기준 비용 계약 21개 경로 및 현재 원장 이력을 포함한 모델상 예산 적합성
- 기존 DEMO 키 세션이 있으면 최대 4회의 GET: 서버 시간, 공개 한도, 계정 주문 사용량 두 번

새 키 입력은 요구하지 않습니다. 세션이 없거나 만료되면 HTTP 요청 없이 `DEMO_SESSION_UNAVAILABLE`을 기록하고 로컬 점검 결과를 표시합니다. 키를 파일/출력으로 복사하지 않습니다. 외부 조회를 생략하려면 `RUN_LOCAL_REVIEW.cmd`를 실행합니다.

## 결과 읽기

로컬 점검 성공은 `runtime_readiness_review: LOCAL_CONTROLS_VERIFIED_KEEP_PAUSED`, `installed_controls_verified: true`입니다. `runtime_installed_by_this_review: false`는 이번 명령이 설치 작업을 하지 않았다는 뜻입니다. 기존 설치 성공을 부정하는 값이 아닙니다.

`documented_cost_contract`와 `modeled_window_capacity`는 문서 및 모델상의 검사 결과입니다. 거래소 실측 비용 상한이나 실제 공용 IP 예약을 증명하지 않습니다. 원래 6,000 한도 값을 반복 표시하는 것과 구분하여 실제 설치, 문서 계약, 모델 적합성, 외부 주문 사용량을 각각 표시합니다.

사용량이 빠진 거래소 응답은 `LIMITS_VERIFIED_USAGE_UNAVAILABLE`과 `ORDER_COUNTER_NOT_PROVIDED`로 표시합니다. 누락 값을 0으로 취급하지 않습니다. 이 경우 같은 조회를 반복해도 해결을 보장할 수 없으므로 자동 재시도하지 않습니다. 제한 응답도 재시도하지 않습니다.

`execution_blockers`가 남는 것은 설치 실패를 의미하지 않습니다. 실제 주문 사용량 확인, 거래소 비용 상한, 공용 IP의 공유 용량 예약, 최신 계정 노출 확인, 실제 실행 기본 차단은 별도 조건입니다. 이 명령은 포지션/미체결 주문 전체를 새로 조회하지 않으며 거래 준비 완료나 거래 승인을 선언하지 않습니다. `execution_approved`, `orders_enabled`, `live_trading_enabled`는 계속 false입니다.

계정 리스·주문 writer 세션·합성 런타임을 시작하지 않습니다. DB·SQLite 저널·기준금액·기간·이력을 수정하지 않고, 디렉터리나 저장소를 생성하지 않습니다. 컨테이너가 꺼져 있거나 날짜가 변경되면 자동 기동/기준값 갱신 없이 중단합니다. 출력은 상태 정보이며 CMD 명령이 아닙니다.

## 검증과 범위

격리 PostgreSQL/SQLite 및 합성 HTTP에서 신규 테스트 16개, 관련 기존 회귀 테스트 326개가 통과했습니다. Docker 네트워크를 끈 상태로 구버전 코드·LIVE 문맥·DB 연결 불가 3개 중단 경로를 확인했습니다. 실제 Windows PC 및 실제 거래소 응답은 이 클라우드 테스트에서 실행하지 않았습니다. PowerShell 해시 검증은 PowerShell 7에서 별도로 확인했습니다.

`verification`은 재현 자료이며 실행 CMD는 그 안의 적용/설정 테스트 자료를 호출하지 않습니다. 소스 기준 커밋은 `e03b6ba683732cd5872c30cf0d245a5b1fb6d325`입니다. 해당 소스 checkout과 개발 의존성이 있는 환경에서, PostgreSQL 테스트 DB를 지정하고 아래처럼 실행합니다.

```sh
PYTHONPATH="$PWD" TEST_DATABASE_URL=postgresql+psycopg://dev:dev@127.0.0.1:55435/hybrid pytest -q /압축해제경로/verification/runtime-readiness-review-20261111/test_runtime_readiness.py
pytest -q tests/unit/test_demo_admission_read_probe.py tests/unit/test_demo_order_admission.py tests/unit/test_runtime_capacity.py tests/unit/test_runtime_cost_contract.py
```
