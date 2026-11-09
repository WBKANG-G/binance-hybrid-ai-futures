# 위험 알림 전달 상태 표시

메일 설정이 없는 동안 실제 비상 알림이 약85분 대기했던 문제를 운영 화면에서 확인할 수 있도록 하는 조회 기능이다. 현재 계정의 위험 상태를 변경하거나 메일 발송을 실행하지 않는다.

새 ZIP을 풀고 UPDATE-ALERT-VISIBILITY.cmd가 보이는 위치에서 CMD를 열어 실행한다.

```bat
UPDATE-ALERT-VISIBILITY.cmd
```

전체 전송 파일 SHA256을 확인하고 기존 읽기 전용 준비 검사를 수행한다. 이후 기존 hybrid-windows 프로젝트의 api와 web만 빌드/갱신한다. db, key-session, alerts 서비스는 갱신하지 않는다. 기존 RAM 키·메일 설정을 지우거나 연장하지 않는다. 설정·의존성·DB 마이그레이션 파일은 기존 버전 그대로다. API의 기존 시작 명령은 변경하지 않았다.

성공 시 SOURCE_AND_READ_ONLY_ENDPOINT_VERIFIED, WEB_SOURCE_AND_ALERT_PROXY_VERIFIED, ALERT_VISIBILITY_UPDATED_ENTRIES_STILL_PAUSED가 표시된다. http://127.0.0.1:5173 을 열고 Ctrl+F5로 새로고침한다. 준비 검사에서 막히면 출력 결과를 공유한다. 설치 명령은 거래 잠금을 해제하거나 과거 요청을 지우지 않는다.

화면의 위험 알림 전달 상태에는 메일 설정 유무·만료 시각(KST), 실제 위험 알림/시험 알림별 대기와 수락 건수, 가장 오래 대기한 시간, 최근 위험 알림의 발생부터 전송 수락까지 걸린 시간이 나온다. 30초마다 갱신하고 조회 실패 시 이전 정상 표시를 제거한다. 메일 설정 등록은 작업자 정상 실행의 증거가 아니며, 수신처 ACK는 받은편지함 도착 증거가 아니다. 이미 수신한 과거 비상 알림은 현재 NORMAL 계정 상태와 다를 수 있다.

메일 설정은 기존과 같이 별도 private RAM에서 최대12시간만 유효하다. 이 화면은 메일 주소·앱 비밀번호·키·주문/계정 식별자를 받거나 표시하지 않는다. 만료/미설정일 경우 기존 수동 등록 절차가 필요하다. 이번 설치는 새 메일/시험 알림/주문을 만들지 않으며 거래·위험 정책을 변경하지 않는다. 기존 알림 작업자가 독립적으로 처리하는 큐는 계속 동작할 수 있다.

API GET /api/v1/demo/alerts/status는 고정 DEMO 계정의 알림을 읽는다. PostgreSQL REPEATABLE READ, READ ONLY 스냅샷의 두 SELECT로 집계와 최근 위험 알림을 조회한다. 이메일 private RAM에서는 허용된 상태/유효기한 숫자만 반환한다. DB 조회 실패는 503/UNKNOWN으로 처리하며 대기0건으로 표시하지 않는다. 실패 원문, recipient, payload, 토큰은 응답에 포함하지 않는다. 확인 시각과 대기 시간은 조회 시점 기준이며 실시간 전달 SLA가 아니다.

이 패키지는 이전 opposite-tp-review 소스의 후속이다. 새 DEMO 실행, 실제 TP/부분체결 수락, Phase4 전체 수락, Phase5, LIVE 또는 main 병합은 포함하지 않는다. ZIP에 보존된 이전 RUN-ONE-OPPOSITE-TP.cmd는 이번 설치 명령이 아니다.
