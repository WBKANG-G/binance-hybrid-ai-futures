# 네이버 본인 이메일 긴급 경고 설정

이 버전은 DEMO 위험 상태 변경과 함께 긴급 경고를 DB 큐에 기록하고, 별도 alerts worker가 이메일로 전달합니다. 알림 전송은 거래 처리 트랜잭션 밖에서 수행합니다. 실제 주문·청산·복구 승인을 추가 실행하지 않습니다. 기존 API 키는 같은 RAM 세션에서 재사용합니다.

## 준비

네이버 메일 환경설정의 POP3/SMTP 또는 IMAP/SMTP 외부 메일 사용 설정을 확인하고, 네이버 계정 보안 설정에서 메일용 애플리케이션 비밀번호를 준비합니다. 일반 로그인 비밀번호 대신 발급한 앱 비밀번호를 사용합니다. 주소·비밀번호를 채팅에 보내지 않습니다. 현재 버전은 본인 @naver.com 주소 하나를 발신·수신에 함께 사용하며 다른 수신자를 지정할 수 없습니다.

SMTP는 smtp.naver.com:465의 인증서를 검증하는 implicit TLS만 사용합니다. TLS 검사 해제나 평문 SMTP로 우회하지 않습니다. 실제 네이버 인증·수신은 사용자 PC에서 아래 명시적 테스트로 확인해야 하며 클라우드에서 실제 키·메일 계정을 사용하지 않았습니다.

## 1. 설치

새 windows-demo-urgent-alert-queue 폴더에 ZIP을 풀고 해당 폴더의 탐색기 주소창에서 cmd를 엽니다. 같은 hybrid-windows 프로젝트와 DB/export/RAM 볼륨을 유지합니다. 이번 빌드는 알림 큐/시도 기록 두 테이블을 추가하는0004 migration을 실행합니다. 기존 장부·감사·outbox를 재작성하거나 과거 경고를 소급 생성하지 않습니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

api/db/web/key-session/alerts가 Healthy인지 확인합니다. alerts의 Healthy는 worker 동작 확인이며 이메일 설정이나 실제 수신 성공을 뜻하지 않습니다. 이메일 설정이 없으면 worker는 대기하며 발송하지 않습니다. 유효한 RAM API 키는 재빌드 후 유지됩니다.

이메일 설정은 거래소 키나 신규 거래를 요구하지 않습니다. 기존 진입 정지 상태를 유지하며 메일만 설정합니다.

## 2. 이메일 주소와 앱 비밀번호 한 번 입력

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.demo_alert_email_session --enable-naver-email
```

본인 네이버 주소를 입력하고 앱 비밀번호는 숨김 입력합니다. 정상 출력은 email_session: CONFIGURED_PRIVATE_RAM입니다. 유효한 이메일 설정이 이미 있으면 다시 묻지 않고 만료 시각도 연장하지 않습니다. 이 명령의 실행은 대기 중인 DEMO 긴급 경고를 본인 메일로 자동 전달하도록 활성화하는 단계입니다. 입력 화면 대신 마지막 JSON만 공유합니다.

주소/앱 비밀번호는 키와 같은 보호된 tmpfs 안의 별도 email.json 파일에만 보관합니다. 디렉터리0700/파일0600/사용자10001, noexec/nosuid/nodev 및 NOFOLLOW/단일 링크 검사를 사용합니다. API 키 session.json은 변경하지 않습니다. .env/DB/이미지/호스트 일반 파일에 앱 비밀번호를 저장하지 않습니다. RAM 안의 값은 평문이며 동일 사용자/root와 호스트 메모리 보호에 의존합니다.

등록부터12시간 유효하고 API 재생성 동안 RAM 마운트가 유지되면 재사용합니다. Docker/컴퓨터 재시작·마지막 마운트 해제·만료 후에는 다시 설정해야 합니다. 만료된 설정으로 새 발송은 시작하지 않으며 대기 알림은 DB에 남습니다. 이미 시작한 SMTP 요청은 제한된 시간 안에 끝날 수 있습니다. 이 구성은24시간 무인 영구 비밀 저장소가 아닙니다.

## 3. 본인에게 테스트 메일 한 번 발송

아래 명령은 실제 네이버 본인 주소로 테스트 메일을 한 통 보냅니다. 메일 제목은 [DEMO] 긴급 알림 수신 테스트이며, 주문을 실행하지 않는 테스트라는 내용입니다. DB에 가짜 거래/긴급 사건을 만들지 않습니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.send_demo_alert_email_test --send-self-test --operation-id b330d024-7b53-4d33-9f8e-9cf358e9bb92
```

SMTP_ACCEPTED_CHECK_YOUR_INBOX는 네이버 SMTP가 수락했다는 뜻입니다. 실제 받은메일함/스팸함에 도착했는지 확인해야 수신 검증을 완료할 수 있습니다. SMTP 수락을 사람이 읽었다는 증거로 취급하지 않습니다. NOT_CONFIRMED_DO_NOT_REPEAT면 즉시 반복 발송하지 않고 고정 실패 코드만 공유합니다. 비밀번호나 SMTP 응답 원문은 보내지 않습니다.

## 4. 알림 큐 상태 확인

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.review_demo_alerts
```

기본 명령은 읽기 전용입니다. email_session이 CONFIGURED_PRIVATE_RAM이고 external_receiver_configured가 true인지 확인합니다. total/pending/delivered/retry_pending은 새 알림 큐 집계이며 테스트 메일은 포함하지 않습니다. 과거 위험 이력의 소급 발송이 없어 새 사건이 없으면0이 정상입니다.

메일 worker는 유효 설정이 있으면 새 긴급 경고를 자동 처리하고, 전송 실패는5초부터 최대300초까지 간격을 늘려 재시도합니다. 알림 ID와 Message-ID는 유지하지만 SMTP에는 일반적인 중복 제거 보장이 없습니다. 응답 유실 뒤에는 같은 메일이 중복 도착할 수 있으며 정확히 한 번 수신을 보장하지 않습니다. 실패 원문/전체 장부/거래소 응답/키는 메일에 포함하지 않습니다.

이메일 설정만 종료하거나 교체할 때 아래 명령으로 이메일 RAM 파일만 지울 수 있습니다. 거래소 키는 지우지 않습니다. 이번 정상 설정에서는 실행할 필요가 없습니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.demo_alert_email_session --clear
```

만료·잘못된 파일을 자동 삭제하거나 다른 계정으로 바꾸지 않습니다. 설정 초기화 뒤에는 명시적으로 다시 등록해야 합니다. 기존 DB/export 볼륨을 삭제하지 않습니다. 이번 배포는 기존 미완료 부분체결/TP 실제 체결이나 Phase4 전체 완료·LIVE/Phase5 전환을 선언하지 않습니다.

SMTP_AUTH_FAILED가 확인되면 worker는 같은 설정으로 로그인을 반복하지 않고 설정 변경을 기다립니다. 대기 알림과 실패 시도는 보존되며, 이메일 설정을 명시적으로 교체한 뒤 재개합니다. worker 재시작 후에는 설정을 다시 확인하므로 같은 잘못된 비밀번호로 서비스를 반복 재시작하지 않습니다.
