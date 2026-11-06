# 실제 네이버 메일 도착 확인

근거는 사용자 CMD 결과와 네이버 메일 수신 화면이다. 배포는 windows-demo-alert-ready-fix-20261101 / 2e770ee, ZIP SHA256 a5d1637dec31fdea19cd0208bb5afaa8a2d310d6ca1d63ed30633178c9b32138이다. 이메일 주소나 비밀번호를 이 문서에 전사하지 않고 원본 캡처 파일 자체도 포함하지 않는다.

하루 뒤 Docker 엔진 미기동을 확인한 뒤 사용자가 Docker Desktop을 실행하고 같은 hybrid-windows 프로젝트를 up -d --wait로 기동했다. api/db/web/key-session/alerts 모두 Healthy였다. 알림 집계는 total/pending/delivered/retry_pending0, email_session NOT_CONFIGURED 및 external_receiver_configured false였다. 이는 Docker 재시작 이후 RAM 메일 설정이 없던 상태의 관측이다.

운영자는 기존 네이버 앱 비밀번호로 명시적 설정을 다시 등록했고 CONFIGURED_PRIVATE_RAM, maximum_lifetime_hours12, persistent_password_storage false, recipient_scope NAVER_AUTHENTICATED_SELF_ONLY를 확인했다. 거래소 API 키는 이 메일 단계에 사용하지 않았다.

새 self-test 작업c493b5c2-c996-4ef6-99a4-2b252b7c0c31은 SMTP_ACCEPTED_CHECK_YOUR_INBOX, orders_enabled false, live_trading_enabled false를 반환했다. 사용자가 공유한 실제 메일 화면의 제목은 [DEMO] 긴급 알림 수신 테스트였고 본문의 알림 ID가 같은c493b5c2-c996-4ef6-99a4-2b252b7c0c31이었다. 표시 시각은 한국시간2026-11-02 월요일15:25였다. 실제 주문·취소·청산을 실행하지 않는 본인 수신 테스트라는 본문도 일치했다.

이로써 명시적 직접 self-test의 실제 네이버 SMTP 인증/수락 및 메일 도착 범위가 통과했다. 이 명령은 DB 큐를 거치지 않으므로 자동 긴급 경고의 전체 경로를 같은 결과로 수락하지 않는다. 과거b330d024 테스트의 수신을 이 화면으로 소급 확정하지 않는다. 다음은 별도 TEST 유형 큐 등록→자동 worker→실제 수신의 독립 검증이다. 실제 손절 실패나 거래 위험을 새로 만들지 않는다.
