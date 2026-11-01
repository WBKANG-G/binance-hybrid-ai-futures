# DEMO 긴급 경고 큐와 네이버 이메일 전달 검증

부모 ZIP은 windows-demo-protected-worker-review / SHA256 297f64bf8858b8e03f417ce6e07e5d3396c99dbad7b2d1df826f7a79c07b44f1다. 기존 파일 변경은 DemoStore.change의 알림 큐 원자적 기록 연결과 별도 alerts worker의 Compose 등록뿐이다. 원래 coordinator/SDK/리스크·수량·시계60초/거래 리스60초/펜스·기준금/기존 키 도우미와 exports는 변경하지 않는다.

## 생성과 저장

0004 migration은 demo_urgent_alerts와 demo_urgent_alert_attempts만 추가한다. 기존 장부·audit·outbox를 수정하지 않으며 과거 사건을 소급 발송하지 않는다. 실제 기존0003 상태에서 업그레이드 전후 장부/감사/outbox 행 완전 일치 및 Alembic metadata 비교를 검사했다.

알림은 원래 위험 상태/요청 상태의 새 변화로 생성한다. STOP_PROTECTION_FAILURE, EXIT_OUTCOME_UNKNOWN, EMERGENCY_ENTERED, RISK_LOCK_ENTERED, PRIVATE_SESSION_FAILED를 고정 코드로 사용한다. EMERGENCY_ENTERED는 운영자의 명시적 비상 명령도 포함하며 항상 기술적 장애를 뜻하지 않는다. 오래된 UNKNOWN이나 heartbeat를 새 실패로 반복 발송하지 않는다. 같은 source event/code의 알림 ID는 결정적 UUID이고 중복 제약을 둔다.

위험 변경·기존 이벤트/outbox·알림을 한 PostgreSQL 트랜잭션에 기록한다. 알림 INSERT 때 실제 시험 backend를 종료하여 모두 함께 롤백되는지 확인했다. 새로운 네트워크 요청은 거래 트랜잭션에서 수행하지 않는다. DB 장애에서는 기존 durable intent/동일 ID 대조 경로를 유지하며 알림 저장 실패를 조용히 무시하지 않는다.

메일 payload는 DEMO/CRITICAL·고정 코드·사건 UUID·계좌 해시 참조·UTC 시각·위험 상태·정지 여부·신호 UUID만 허용한다. 전체 장부·operations·원래 오류/응답·키·서명·SMTP 주소/비밀번호를 넣지 않는다. 변조/추가 필드가 있는 payload는 발송하지 않고 고정 코드로 실패를 기록한다.

## 전달·재시도

claim은 짧은 독립 트랜잭션과 FOR UPDATE SKIP LOCKED로 획득하고 HTTP/SMTP 전송 전에 COMMIT한다. 전송 시도는 별도 기록으로 남는다. 만료된30초 알림 claim은 새 token으로 재인수하며 이전 소유자의 완료 처리를 거부한다. 이30초는 거래 리스60초와 별개다. 실패는5초부터300초까지 지수 간격으로 재시도하며 대기 건이나 과거 시도를 지우지 않는다.

수신 실패·잘못된 ACK·수신 후 응답 유실·수신 ACK 뒤 DB 저장 실패·경쟁 전달기·만료 claim을 검사했다. 로컬 HTTP 수신기의 중복 제거는 같은 alert_id/Idempotency-Key를 사용하지만 이를 SMTP에도 일반화하지 않는다. SMTP는 같은 Message-ID로 최소 한 번 전달을 재시도하므로 응답 유실 뒤 중복 수신이 가능하다.

네이버 sink는 smtp.naver.com:465, 인증서 검증 implicit TLS, 본인 주소 From=To로 제한한다. 앱 비밀번호는 별도 보호된 RAM 파일에만12시간 보관하며 재사용/만료 불변·잘못된 권한/링크/파일/시간·다른 수신 주소·헤더 삽입·영구 파일시스템 거부를 검사했다. 같은 디렉터리의 원래 API 키 파일이 바뀌지 않는지 확인했다. 캐시 입력 자체는 SMTP 인증이나 메일 수신 성공을 증명하지 않는다.

실제 로컬 TLS SMTP 서버로 AUTH/MAIL/RCPT/DATA와 인증 실패 후 재시도를 검증했다. 기본 SSLContext의 hostname/CERT_REQUIRED를 요구하고 시험 factory만 로컬 인증서로 라우팅했다. 실제 네이버에 클라우드 인증 연결을 하지 않았다. SMTP 코드/예외는 고정 실패 코드만 보존하고 비밀번호/주소/응답 원문을 로그에 내보내지 않는다. 송신 budget은10초이며 이는 DNS 및 연결이 오래 걸릴 때의 외부 OS 지연 자체까지 보장하는 의미는 아니다. 예산이 지난 SMTP 명령은 전송하지 않는다.

## 운영 기본값과 실제 수신

새 alerts 컨테이너는 읽기 전용 root/RAM 마운트·cap drop·no-new-privileges·호스트 포트 없음으로 실행한다. API migration/health 뒤 시작한다. SMTP 설정이 없으면 발송 없이 대기한다. worker Healthy는 생존 신호이며 SMTP 인증/받은메일함 확인을 뜻하지 않는다. 유효한 본인 설정을 명시적으로 등록하면 대기·새 긴급 경고를 자동 발송한다. 만료 시에는 새 발송을 멈추고 DB 대기 기록을 남긴다.

발송 완료 표시는 채널 수락 기준이다. 실제 네이버 SMTP 수락과 본인 받은메일함/스팸함 수신은 사용자 PC의 명시적 테스트 메일 결과로 별도 확인해야 한다. 테스트 메일은 실제 주문이나 가짜 사건/경고를 DB에 만들지 않는다. SMTP 수락은 사람의 열람·최종 수신 보장이 아니다.

검증 수치: 전체1339개 통과(396.68초), 집중48개 통과(10.00초), SMTP 인증 거부 대기 보완 후 worker/CLI16개 재검증 통과(0.32초). 실패·건너뛰기 없음. Ruff 검사/포맷 검사, mypy121개 소스 및 Compose 구성 검사 통과.

격리 PostgreSQL, 로컬 HTTP/TLS SMTP 및 합성 거래소로 검증했으며 실제 Binance/네이버 인증이나 사용자 메일 발송은 클라우드에서 하지 않았다. 실제 이메일 수신·장기 무인 운영과 미완료 부분체결/TP 트리거·실제 계좌 장애 수락은 별도다. main/LIVE/Phase5 및 Phase4 전체 완료 판정은 변경하지 않았다.

SMTP_AUTH_FAILED가 확인되면 worker는 같은 설정으로 로그인을 반복하지 않고 설정 변경을 기다립니다. 대기 알림과 실패 시도는 보존되며, 이메일 설정을 명시적으로 교체한 뒤 재개합니다. worker 재시작 후에는 설정을 다시 확인하므로 같은 잘못된 비밀번호로 서비스를 반복 재시작하지 않습니다.

DB 전체가 사용 불가하면 새 경고의 원자적 기록과 전달 상태 갱신도 불가합니다. 이때의 독립적인 외부 DB 가용성 감시·장애 통보는 이번 큐 기반 경로의 수락 범위에 포함하지 않습니다.
