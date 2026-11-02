# 기존 설치에서 읽기 전용 토큰 진단

두 적용 시도는 PERIOD_PREVIEW_CHANGED로 차단됐고 일간·주간 기준금은 미적용 상태다. 어시스턴트가 전사 오류를 원인으로 단정하고 정정 토큰을 제시했지만, 그 정정 명령도 차단됐다. 따라서 전사 오류만으로 원인을 확정하지 않는다. 이 도구는 원인을 비교하는 진단이며 적용 도구가 아니다.

ZIP의3개 파일을 기존 windows-demo-paused-period-apply 폴더에 푼다. 앱 코드·기존 파일을 덮어쓰거나 빌드/재시작하지 않는다. 이 폴더의 CMD에서 아래 전체 명령 한 건만 실행한다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python - --attest-no-activity-since-anchor < diagnose_demo_period_token.py
```

호스트 파일의 진단 코드를 기존 컨테이너 Python으로 전달해 실행한다. 현재 유효한 DEMO 키 RAM 설정만 읽고 입력은 요청하지 않는다. 키가 없거나 만료되면 BLOCKED_NO_APPLY/CACHED_DEMO_KEY를 보고하므로 그대로 공유한다. 이 진단을 위해 키를 재등록/삭제하거나 메일 비밀번호를 입력하지 않는다.

성공 결과는 READ_ONLY_PERIOD_TOKEN_COMPARISON이다. 토큰을8자리씩 나눠 표시하며 initial_command_token_matches/corrected_command_token_matches, 토큰 수식 재현, 리더 획득 때의 플래그 변경 및 JSON 직렬화 모의를 비교한다. normalized_* 해시와 changed_top_level_fields는 원시 장부 대신 해시/필드명만 제공한다. 현재 두 기준금 후보와 증거 해시·fingerprint도 함께 출력한다. 결과 전체를 공유하고 apply 명령은 실행하지 않는다.

진단은 현재 서명 증거를 새로 한 번 조회한다. 기존40초/600 GET/6 probe 예산과 GET 전용 검토를 사용한다. 모든 DB 트랜잭션에 READ ONLY를 적용하고 SQL도 SELECT만 허용한다. 정상 조회 전후 계좌 state/owner/fence/lease의 동일성을 확인한다. 실제 리더 획득/감사 저장/기준금 적용/주문·취소·청산/잠금 해제/메일 발송은 없다.

모의 플래그/JSON 비교가 일치하더라도 실제 적용 트랜잭션에서의 원격 관측 차이까지 입증하지는 않는다. 이 진단 결과만으로 임의의 새 토큰을 승인하거나 기존 비교를 우회하지 않는다. 새 애플리케이션 버전이나 전체 Phase4 완료가 아니다.

## 사용자 실제 진단 결과

initial_command_token_matches와 corrected_command_token_matches가 모두 false로 확인됐습니다. 수식 재현/획득 플래그 모의/JSON roundtrip은 모두 true였고 장부 해시가 같으며 변경 필드 목록은 비어 있었습니다. 즉 실제 생성된 값과 두 안내 문자열이 달랐습니다. 계좌 owner/fence/lease/state는 그대로였고 기준금 적용은 없었습니다.

실제8자리 묶음은 bff38c19 / 98320344 / e9c03411 / b83a9369 / b152bfca / a76f50fb / 23438fc8 / 9cdf7a1c이며 프로그램으로 결합한 실제 토큰은 bff38c1998320344e9c03411b83a9369b152bfcaa76f50fb23438fc89cdf7a1c입니다. 후보 두 기준일2026-11-02와 두 기준금4998.99761493 USDT 및 기존 증거 해시/fingerprint도 같았습니다. 안내 토큰의 오독을 정정했으며 적용 도구 자체나 수락 조건은 변경하지 않습니다. 실제 적용 성공은 아직 후속 출력 전입니다.
