# 현재 알림 작업자 읽기 전용 관찰

Windows에서 알림 조회 API와 웹 배포가 성공했다. 메일 설정 CONFIGURED_PRIVATE_RAM, 위험/시험 대기 각각0건, NORMAL/paused 및 원격 빈 계정도 확인됐다. 이번에는 아직 측정하지 않은 현재 작업자의 heartbeat 진행과 RAM 메일 설정 공유 상태만 확인한다.

ZIP을 새 폴더에 풀고 CHECK-ALERT-WORKER.cmd가 보이는 위치에서 CMD로 실행한다.

```bat
CHECK-ALERT-WORKER.cmd
```

기존 hybrid-windows-alerts-1의 실행/Health 상태를 표시하고, 보조 코드의 SHA256을 검사한 뒤 STDIN으로 컨테이너 안에서 실행한다. 소스/이미지/DB/키/메일 설정을 갱신하거나 컨테이너를 재시작하지 않는다. 호스트 Python은 필요하지 않으며 기존 Docker와 Windows PowerShell을 사용한다.

관찰은 heartbeat 파일을3초 간격으로 두 번 읽어 최신성(<30초)과 증가 여부를 확인한다. 기존 메일 설정 함수는 상태 문자열만 반환한다. 작업자에서 Docker 내부 API의 고정 GET /api/v1/demo/alerts/status를 조회해 설정 가시성과 대기 건수를 비교한다. 프록시와 HTTP 리다이렉트는 사용하지 않는다. 키/메일 주소/앱 비밀번호/원문 응답/예외를 출력하지 않는다.

HEARTBEAT_ADVANCING_CONFIG_VISIBLE이면 관찰 구간에서 heartbeat 진행과 같은 유효 설정의 가시성이 확인된 것이다. 이 결과는 SMTP 인증, 실제 발송 가능성, 받은편지함 도착을 검증하지 않는다. delivery_readiness_verified/smtp_authentication_checked/inbox_checked는 false로 남는다. 과거 실제 경고 메일 수신 증거와 구분한다.

REVIEW_REQUIRED_KEEP_PAUSED 또는 UNAVAILABLE_KEEP_PAUSED이면 출력 그대로 공유한다. 루프가 SMTP 처리 중이면3초 안에 heartbeat 증가가 관찰되지 않을 수도 있다. 이 결과만으로 작업자 고장으로 단정하거나 재시작/메일 설정 초기화/주문 재시도를 하지 않는다. 재진입 잠금은 계속 유지한다.

진단은 새 알림/메일/주문을 만들거나 SMTP 서버에 접속하지 않는다. 별도로 실행 중인 기존 알림 작업자의 정상 큐 처리는 계속될 수 있다. API의 DB 조회는 기존 읽기 전용 구현을 사용한다. 진단 중 상태가 바뀌면 설정 불일치로 검토 필요를 표시한다.

EVIDENCE_INDEX.json과 ACCEPTANCE_MAP_KO.md는 이번까지 사용자 Windows 출력으로 확인한 검증 기록이다. 이 진단의 실제 Windows 결과와 브라우저 화면 육안 검사는 아직 대기 중이다. 실제 TP 발동/원래 SL 정리, 수정 후 실제 부분체결 보호, 포지션 보유 중 장애 검증은 여전히 미완료다. Phase4 전체 수락/Phase5/LIVE/main 병합을 수행하지 않는다.
