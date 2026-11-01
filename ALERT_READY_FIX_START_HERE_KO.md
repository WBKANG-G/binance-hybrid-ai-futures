# 이메일 버전 API unhealthy 수정

부모 이메일 ZIP은 DB migration을0004_demo_urgent_alerts로 올렸지만 API의 준비 검사는0003_demo만 허용했다. 이 불일치 때문에 DB migration이 정상이어도 준비 API가503을 반환하고 alerts의 depends_on이 시작을 막는 문제가 있었다. 이미지 빌드나 메일 앱 비밀번호 문제가 아니다.

수정 버전은 준비 검사에서0004_demo_urgent_alerts를 정확히 허용한다. 알 수 없는/누락/여러 revision과 DB 장애는 그대로503으로 차단한다. 새 migration이 추가될 때 readiness와 현재 Alembic head의 일치를 검사하는 회귀 시험을 추가했다.

## 적용

새 windows-demo-alert-ready-fix 폴더에 ZIP을 풀고 해당 폴더 탐색기 주소창에서 cmd를 실행한다. 기존 hybrid-windows 프로젝트 이름을 그대로 사용한다. 기존 DB/export/RAM 키와 이메일 설정 파일을 유지한다. 별도 DB migration 변경이나 데이터 초기화가 없다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

api/db/web/key-session/alerts가 모두 Healthy인지 확인하고 결과를 공유한다. 비밀번호 설정은 이 빌드가 정상 완료된 뒤 진행한다. 여전히 unhealthy면 아래 API 로그로 새 원인을 확인하며 반복 빌드나 볼륨 삭제를 하지 않는다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml logs --tail 80 api
```

## 빌드 이후 이메일 설정

아직 이메일 설정을 하지 않았다면 네이버 SMTP 사용 설정과 앱 비밀번호를 준비한 뒤 아래 명령으로 본인 주소와 앱 비밀번호를 한 번 입력한다. 거래소 키는 다시 입력하지 않는다. 유효한 메일 RAM 설정이 있으면 재입력을 요구하지 않는다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.demo_alert_email_session --enable-naver-email
```

설정 활성화 후 대기/새 긴급 경고를 본인 메일로 자동 전송할 수 있다. 일반 네이버 로그인 비밀번호 대신 앱 비밀번호를 숨김 입력하며 주소/앱 비밀번호는 채팅에 보내지 않는다.

아래 이전에 안내한 테스트는 아직 실행하지 않았을 때만 한 번 진행한다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.send_demo_alert_email_test --send-self-test --operation-id b330d024-7b53-4d33-9f8e-9cf358e9bb92
```

SMTP_ACCEPTED_CHECK_YOUR_INBOX와 받은메일함/스팸함의 실제 도착을 함께 확인한다. 이번 API 수정은 실제 메일 수신 성공을 대신하지 않는다. 오류가 불명확하면 반복 발송하지 않고 고정 JSON 결과만 공유한다. 이번 적용은 주문·청산·복구 승인이나 Phase4 전체 완료/LIVE/Phase5 전환을 요구하지 않는다.
