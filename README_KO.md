# income 필터 읽기 전용 비교 — 2026-11-07 (한국시간)

실제 DEMO 서버에서 전체 income 조회와 TRANSFER/WELCOME_BONUS 필터가 같은 결과를 주는지 확인하는 도구입니다. 운영 코드·이미지·스키마를 설치하거나 바꾸지 않습니다. **주문 제출/취소, 복구, 기간 기준값 변경, 리더 획득, 키 등록을 하지 않습니다.** 이번 클라우드 검증에서는 실제 계좌 API를 호출하지 않았습니다.

## Windows에서 실행

ZIP을 바탕화면의 `windows-demo-tp-income-filter-review` 폴더에 풀고 CMD에서 다음 두 줄을 입력합니다. `RUN_COMMAND.txt`에도 같은 명령이 있습니다. Docker 컨테이너는 기존 `hybrid-windows-api-1`을 사용합니다.

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-tp-income-filter-review"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'compare_income_filters.py').Hash.ToLower() -ne '02e5441659047dadb2eca0153f74378e8f20e1b68a1b66011914d988398045bb'){throw 'INCOME_REVIEW_HASH_MISMATCH'}" && docker exec -i hybrid-windows-api-1 python - < compare_income_filters.py
```

파일 해시가 다르면 Docker 실행으로 이어지지 않습니다. helper는 컨테이너 내부의 배포 기준 e9327bd4729c7b5a4eeebff0e309f0f631395098 관련 소스 29개도 검사합니다. 다른 후보를 설치했다면 소스 불일치로 거부될 수 있습니다. 이때 승인 해시를 수정하거나 다시 설치하지 말고 차단 결과를 확인해야 합니다.

기존 비공개 DEMO 키 세션을 읽기만 합니다. `DEMO_CREDENTIAL_SESSION_UNAVAILABLE`이면 세션 만료/부재/잘못된 세션으로 진행하지 못한 것입니다. 도구는 키를 입력받거나 갱신하지 않습니다. API 키·시크릿·세션 파일을 채팅이나 명령줄에 넣지 마십시오. 공유할 것은 결과 JSON뿐입니다.

## 비교 범위

서버 시각보다 10초 이전을 종료 시각으로 고정하고, 로컬 income 시작 시각 또는 최근 24시간 중 짧은 구간을 사용합니다. 그 구간에 대해 다음 GET을 수행합니다.

1. 전체 income 조회.
2. TRANSFER 필터 조회.
3. WELCOME_BONUS 필터 조회.
4. 동일 구간 전체 income 재조회.

전체 조회 두 번의 type/ID/asset/amount/time이 안정적이고, 두 필터의 합집합이 전체 조회에서 같은 두 유형을 뽑은 결과와 같은지 비교합니다. 응답 순서나 Decimal 표기 차이만으로 불일치시키지 않지만, 중복 ID, 잘못된 시각·숫자·자산, 필터가 무시된 응답은 거부합니다. 한 incomeType 안의 ID는 고유해야 하며 서로 다른 유형에서 같은 ID는 허용합니다.

**page 인자는 사용하지 않습니다.** 현재 공식 모듈형 SDK 메서드에는 page가 노출되지 않습니다. 이것만으로 서버가 page를 지원하지 않는다고 단정하지는 않으며, 이번 도구는 그 동작을 가정하지 않습니다. 요청한 1,000건 한도에 도달하면 범위가 모두 조회됐다고 볼 수 없어 즉시 중단합니다. 자동으로 범위를 줄이거나 다음 페이지를 요청하지 않습니다.

## 결과 해석

- `BOUNDED_WINDOW_MATCH`: 선택한 구간에서만 전체/필터 비교가 일치했습니다. 프로세스 종료 코드 0도 이 의미입니다.
- `both_target_types_observed=false`: 두 대상 유형 중 일부 또는 모두의 실물 사례가 없었습니다. 빈 응답 일치는 필터 지원의 충분한 증거가 아닙니다.
- `non_target_type_counts`: 현재 공식 SDK가 알려주는 다른 income 유형의 건수입니다. INTERNAL_TRANSFER 등은 현재 로컬 회계의 두 대상 유형과 별개라 추가 의미 검토가 필요합니다. 알려지지 않은 유형은 원문을 출력하지 않고 `unknown_income_type_rows`로 셉니다.
- `period_keys_match=false`: 현재 계좌의 날짜/주간 키가 다릅니다. 역사 데이터 비교 결과와 함께 표시할 뿐, 복구하거나 거래를 허용하지 않습니다.
- `INCOME_RESULT_AT_LIMIT_COVERAGE_UNPROVEN`: 1,000건에 닿아 완전한 비교를 할 수 없습니다.
- `SIGNED_QUERY_OR_FILTER_SUPPORT_UNVERIFIED`: API 오류·필터 거부 등으로 확인하지 못했습니다. 필터 없는 조회로 자동 재시도하지 않습니다.
- `FILTER_IGNORED_OR_WRONG_TYPE`, `FILTERED_UNION_MISMATCH`, `UNFILTERED_WINDOW_CHANGED`: 비교를 성공 처리하지 않습니다.

항상 `optimization_approved=false`입니다. 이 결과만으로 운영 income 조회를 필터 방식으로 전환하거나 실제 주문을 재개하지 않습니다. 선택 구간 밖의 이력, 다른 income 유형의 위험 회계, 계좌 신원의 독립적인 증명, 전체 수명주기의 비용 상한을 입증하지 않습니다.

필터 조회 2회가 전체 조회 1회보다 반드시 싸지는 않습니다. 각각 1회로 끝나는 작은 구간에서는 income 요청 수가 1회 대 2회입니다. 헤더 가중치 차이는 공유 IP 활동과 카운터 초기화의 영향을 받을 수 있어 비용 증명으로 사용하지 않습니다. 카운터가 줄거나 분이 바뀌면 해당 차이 합계를 생략합니다.

## 읽기 전용 제한

로컬 DB는 READ ONLY/REPEATABLE READ 트랜잭션과 3초 statement timeout으로 읽습니다. NORMAL/paused/owner 없음, 기존 서명된 수동 청산 receipt와 실패 시험 marker를 확인합니다. 전후 owner/fence/lease 및 전체 로컬 상태가 같아야 합니다. 확인 중 다른 작업이 상태를 바꾸면 성공으로 반환하지 않습니다.

SDK는 demo_trading_enabled=false이고, client와 sender에서 경로를 제한합니다. 허용된 네트워크 호출은 time/exchangeInfo, 현재 전역 포지션·일반 주문·조건부 주문, income의 GET뿐입니다. 전후 현재 포지션·주문이 비어 있는지 확인하되, 이를 지속적인 flat 보장이나 세션 준비 완료 판정으로 사용하지 않습니다.

요청 시작 기한 75초, HTTP 최대 32회, 시계 조회 최대 10회입니다. 이미 시작한 요청은 원래 전송 timeout으로 끝납니다. 공개 분당 한도의 60%에서 추가 최대 100 가중치 여유를 두고 멈춥니다. 418/429, 헤더 부재, 응답 불확실성은 자동 재시도하지 않습니다. query/서명/키/원시 income/ID/금액/서버 오류 본문은 출력하지 않습니다.

## 검증과 근거

`VALIDATION.json`과 `test-results/`에 격리 PostgreSQL·합성 HTTP 시험 결과가 있습니다. 실제 Windows/DEMO 결과는 아직 없습니다. 배포 소스는 변경하지 않았고, helper를 원본 source29-file pin과 함께 검사했습니다.

현재 및 이전 공식 SDK의 고정 커밋과 해시는 `CONTRACT_REFERENCE.md/json`에 있습니다. 공식 문서 사이트 직접 접근은 환경에서 403으로 거부됐고, SDK를 설치하거나 실행하지 않고 소스로 계약을 검토했습니다. 현재 소스는 두 필터와 IP 가중치 30을 문서화하지만 DEMO 서버의 실제 응답은 이 도구로 별도 확인해야 합니다.

클라우드 테스트 재현은 배포 기준 소스를 별도 체크아웃하고 기존 의존성과 pytest 설정을 사용하며, 폐기 가능한 PostgreSQL만 연결합니다. 테스트 파일은 원본 소스 디렉터리를 cwd/PYTHONPATH로 사용해야 합니다. 사용자 계좌 DB에서 테스트 파일을 실행하지 마십시오.
