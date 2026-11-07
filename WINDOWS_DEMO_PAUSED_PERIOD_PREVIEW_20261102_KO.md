# 실제 NORMAL/paused 일간·주간 후보 확인

사용자 CMD 결과에 근거한다. 배포 windows-demo-paused-period-preview-20261102 / c97240ce5bff0687ce0c2662102ba5899b871f35, 부모 ZIP SHA256 702413cc2eb35a51171b61a8bfbd49e170163d86c6f162f325851bf8d4e43a31.

api/db/web/key-session/alerts 모두 Healthy였다. 사용자는 DEMO 키를 숨김 입력했고, 출력은 PAUSED_NORMAL_PERIOD_PREVIEW_NO_DATABASE_CHANGE였다. 키 값이나 입력 화면 원본은 저장하지 않는다.

- source OPERATOR_ATTESTED_FLAT_PERIODS_WITH_SIGNED_USDT_EVIDENCE.
- review_token bff38c1998320344e9c03411b83a9369b152bfcaa76f50fb23438fc89cdf7a1c.
- 이전 일간2026-11-01 / 4999.03492670 USDT.
- 이전 주간2026-10-26 / 4999.64804199 USDT.
- 후보 일간·주간 기준일 모두2026-11-02.
- 후보 일간·주간 기준금 모두4998.99761493 USDT.
- 두 경계 모두2026-11-01T15:00:00+00:00.
- anchor_utc2026-11-01T01:55:09.565889+00:00.
- signed_evidence_until_utc2026-11-02T07:47:42.380767+00:00.
- post_anchor_btc_trades0 / post_anchor_income_records0 / net_deposit_basis_usdt0.
- NORMAL/paused, database_changed false, preview_only true.
- 고점·이전 평가 시각·요청·위험 상태 보존 true.
- account_wide_exchange_export_verified/unresolved_requests_acknowledged/recovery_eligible/orders_enabled/live_trading_enabled false.
- POOLED_HTTPS, HTTP170회/연결1회/재사용169회/서버 close0회, 10152ms.
- signed GET167회 / clock probe3회.

사용자가 새로 확인한 다른 활동 없음과 기존 서명 검사에 근거한 후보 수락이다. 자정 잔고 직접 관측이나 계좌 전체 export 검증이 아니다. 미리보기만으로 기준금이 바뀐 것은 아니다. 적용 시 같은 토큰·전체 서명 증거를 다시 검사하며 실패하면 변경하지 않는다. 이후 활동이나 날짜/잔고/장부 변화가 생기면 예전 후보를 강제로 적용하지 않는다. 현재 Windows 실제 적용은 아직 확인 전이다.

## 적용 차단 이후 재조회 및 전사 정정

사용자 적용은 PERIOD_PREVIEW_CHANGED로 차단됐다. 이어 같은 배포의 읽기 전용 재조회에서 같은 후보/증거 해시/거래소 fingerprint와 토큰이 다시 나왔다. 재조회 cutoff는2026-11-02T08:13:02.860123+00:00, HTTP170회/연결1회/재사용169회/9638ms, signed GET167회/clock3회였다.

원인은 어시스턴트가 적용 명령에 검토 토큰을 잘못 옮긴 것이었다. 사용자 화면의 토큰은 bfcf38c1998320344e9c03411b83a9369b152bfca76f50fb23438fc89cdf7a1c이며, 이전 명령의 bcf38c1998320344e9c03411b83a9369b152bfcaa76f50fb23438fc89cdf7a1c는 잘못된 전사였다. 두 값은 모두64자리라 형식 검사는 통과했으나 원래 토큰 일치 검사가 적용을 거부했다. 기준금 적용은 성공하지 않았다.

이번 정정은 명령/증거 문서뿐이며 앱 코드와 토큰 비교·서명/금융 수락 조건을 변경하지 않는다. 이미 설치된 동일 앱에서 올바른 토큰과 같은 미완료 작업 ID로 적용할 수 있다. 다시 배포하거나 새 작업을 만들 필요가 없다. 실제 적용 성공은 후속 JSON 전까지 미확인이다.

## 자동 진단으로 확인된 실제 토큰과 앞선 판독 정정

사용자의 READ_ONLY_PERIOD_TOKEN_COMPARISON 출력은 initial_command_token_matches/corrected_command_token_matches 모두 false였다. 두 안내 문자열 모두 실제 생성값과 달랐다. 원래 토큰 수식 재현, 획득 플래그 모의, JSON roundtrip은 모두 true였고 정규화 장부 해시3개가 같았으며 변경 필드 목록은 비어 있었다.

실제8자리 묶음은 bff38c19 / 98320344 / e9c03411 / b83a9369 / b152bfca / a76f50fb / 23438fc8 / 9cdf7a1c다. 결합한 실제 토큰은 bff38c1998320344e9c03411b83a9369b152bfcaa76f50fb23438fc89cdf7a1c다. 이전 bcf... 및 bfcf... 안내는 어시스턴트의 잘못된 판독이었다. 앞선 '사용자 화면의 올바른 토큰 bfcf...' 문장도 이 진단 관측으로 정정한다. 두 명령이 차단된 직접 원인은 실제 토큰과 다른 문자열이었다. 수락 조건이나 앱 코드는 변경하지 않는다.

후보 두 기준일2026-11-02/두 금액4998.99761493 USDT, 서명 증거 해시/거래소 fingerprint는 이전과 같았다. 계좌 owner/fence/lease/state 불변, apply_attempted false였다. HTTP170회/연결1회/재사용169회/10253ms, signed GET167회/clock3회였다. 올바른 토큰과 같은 미완료 작업 ID7230ec92-b17e-4fa9-bfef-336801a2182e로 원래 서명 재검증을 거쳐 적용할 수 있다. 실제 Windows 적용 성공은 아직 후속 출력 전이다.
