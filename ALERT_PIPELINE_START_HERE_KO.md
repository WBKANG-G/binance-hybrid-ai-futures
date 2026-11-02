# 알림 큐 자동 worker 전달 확인

네이버 직접 self-test는 실제 수신까지 확인됐다. 이번 단계는 등록 명령이 메일을 보내지 않고, DB 큐에 TEST 한 건만 넣어 기존 alerts worker가 자동 전송하는 경로를 확인한다. 실제 주문/취소/청산/위험 상태 변경은 없다. 거래소 API 키가 필요하지 않으며 유효한 이메일 RAM 설정은 재사용한다.

## 1. 설치

새 windows-demo-alert-pipeline-check 폴더에 ZIP을 풀고 해당 폴더 탐색기 주소창에서 cmd를 실행한다. 같은 hybrid-windows 프로젝트를 유지한다. DB revision/금융 계좌/기준금/기존 감사·outbox·RAM 키와 메일 설정을 유지한다. 이번 버전은 새 migration이 없고 readiness는0004_demo_urgent_alerts 그대로다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

모든 컨테이너 Healthy 확인 후 아래 단일 등록을 실행한다. 이메일 캐시가 만료되거나 Docker 재시작으로 없어졌다면 자동 등록/삭제/재입력하지 않고 BLOCKED 결과를 공유한다.

## 2. 테스트 알림 한 건 등록하고 최대30초 관찰

아래는 새 전용 작업 ID다. 이전 직접 self-test ID를 재사용하지 않는다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.check_demo_alert_pipeline --enqueue-self-test --operation-id 4e5d7b91-23a7-4612-b8dc-23542cc63ff0 --wait-seconds 30
```

이 명령은 DB의 알림 테이블만 쓰고 발송/claim을 직접 하지 않는다. 같은 ID의 재호출은 기존 행을 조회하며 delivered_at/시도 기록을 초기화하거나 알림을 다시 추가하지 않는다. 현재 연결 참조 감사 기록당 TEST는 한 건으로 제한해 다른 ID를 만들어 반복 발송하지 못하게 한다.

정상 DB 결과는 QUEUE_DELIVERY_ACKNOWLEDGED, severity TEST, code DELIVERY_PIPELINE_TEST, attempt_results에 ACKNOWLEDGED다. 이는 소비자가 채널 수락을 기록했다는 뜻이며, SMTP 채널/실제 수신은 아래 worker 로그와 메일을 함께 확인해야 한다. 과거 또는 가짜 완료 시각만으로 성공 판정하지 않는다.

## 3. 자동 worker 로그와 실제 메일 대조

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml logs --tail 20 alerts
```

component DEMO_EMAIL_WORKER, channel NAVER_SMTP, alert_delivery DELIVERED와 같은 알림 ID4e5d7b91-23a7-4612-b8dc-23542cc63ff0가 로그에 있는지 확인한다.

받은메일함/스팸함의 새 제목은 [DEMO] 알림 큐 자동 전달 테스트다. 본문의 유형 TEST 및 알림 ID가 같은지 확인한다. 이 새 메일과 등록 명령의 JSON, worker 로그를 함께 공유한다. 주소·비밀번호 입력 화면은 보내지 않는다.

## 완료되지 않은 경우

QUEUED_WAITING_FOR_WORKER, WORKER_PROCESSING, RETRY_PENDING 또는 BLOCKED는 성공으로 표시하지 않는다. 명령의 비정상 종료에 따른 Docker 일반 도움말을 Docker 고장으로 단정하지 않는다. 새 ID를 만들거나 테스트 메일을 직접 추가 발송하지 않는다. 아래 읽기 전용 조회로 같은 ID의 상태만 확인할 수 있다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.check_demo_alert_pipeline --review --operation-id 4e5d7b91-23a7-4612-b8dc-23542cc63ff0
```

SMTP 응답 유실 후 worker 재시도에서는 같은 Message-ID로 여러 메일이 도착할 수 있다. 등록 중복 방지는 SMTP의 정확히 한 번 수신 보장이 아니다. 큐 집계에는 이 TEST도 한 건 포함되며 실제 긴급 사건으로 취급하지 않는다.

테스트는 기존 실제 감사 event_id를 연결 참조로만 사용한다. 새 금융 감사 사건·위험 상태·가짜 체결/평가/기준금을 만들지 않으며 기존 demo_accounts/demo_events/demo_outbox 행을 수정·추가하지 않는다. 기존 UNIQUE(event_id,code) 제약을 유지한다. TEST가 연결된 감사 기록에 실제 장애가 있었다는 뜻이 아니다.

이 검증은 TEST 큐 자동 전달 경로의 수락이다. 실제 손절 장애·부분체결·TP 체결을 강제로 발생시키거나 실제 미완료 항목을 통과로 바꾸지 않는다. 거래를 다시 진행하기 전 바뀐 일간/주간 기준일은 원래 별도 검토 절차가 필요하며 이번 이메일 테스트에서 변경하지 않는다. Phase4 전체 완료/LIVE/Phase5 전환은 없다.
