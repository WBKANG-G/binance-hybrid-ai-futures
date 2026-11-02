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
