# 읽기 전용 진단 검증

이 번들은 설치된 windows-demo-paused-period-apply 앱을 변경하지 않는 별도 stdin 진단이다. 초기/정정 안내의 토큰은 비교 대상으로만 들어 있고 기준금 적용 인자로 사용하지 않는다. 기존 토큰·금융·서명 수락 조건을 수정하지 않는다. 두 번째 적용 차단 이후 원인은 미확정이며, 과거 문서의 '전사 오류가 원인'이라는 단정은 충분하지 않았다.

실제 격리 PG18/기존 합성 서명 fixture로6개 통과(1.90초). 준비 플래그가 true/false인 각각의 장부에서 원래 토큰 수식 재현과 획득 플래그·JSON roundtrip 모의를 검사했다. DB가 실제 transaction_read_only=on임을 확인했고 전체 금융 accounts/events/outbox가 같았다. 계좌 owner/fence/lease도 비교하며 acquire 호출을 금지했다. 원격 요청은 GET뿐이었다. SQL UPDATE를 삽입한 오류 주입은 실행 전에 거부되고 장부가 그대로임을 검사했다. LIVE/확인 없음/키 캐시 없음은 비밀 문자열을 출력하거나 키를 입력받지 않고 고정 BLOCKED_NO_APPLY를 보고했다.

Ruff 검사/포맷4개 파일 및 mypy strict 진단 소스1개 통과. 최초 외부 테스트 수집에서는 앱 경로 미지정으로 packages import가 실패했고, 앱 pyproject.toml을 명시한 원래 조건 실행에서6개가 통과했다. 테스트/판정 조건은 완화하지 않았다. 전체 앱 회귀는 앱 변경이 없으므로 다시 실행하지 않았다. 기존 앱 코드/배포 ZIP은 이 작업에서 바꾸지 않는다.

실행한 검사:

```text
TEST_DATABASE_URL=<격리 PG> pytest -c pyproject.toml -q ../period-token-diagnostic/test_diagnostic.py
mypy --strict ../period-token-diagnostic/diagnose_demo_period_token.py
ruff check --config windows-demo-paused-period-apply/pyproject.toml period-token-diagnostic
ruff format --check --config windows-demo-paused-period-apply/pyproject.toml period-token-diagnostic
```

사용자 Windows 및 실제 서명 관측의 진단 결과는 아직 받기 전이다. 실제 키/외부 거래소/메일은 클라우드 검증에서 사용하지 않았다. 원격 조회 실패나 장부 동시 변경은 진단을 차단하며, 이 도구로 기준금 적용을 우회하거나 후보를 자동 승인하지 않는다. 실제 적용 성공, Phase4 전체 완료/LIVE/Phase5는 수락하지 않는다.
