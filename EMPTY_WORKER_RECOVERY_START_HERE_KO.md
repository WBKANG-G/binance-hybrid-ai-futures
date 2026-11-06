# 빈 계좌의 관찰 프로세스 강제 종료와 리더 복구

새 windows-demo-empty-worker-recovery 폴더의 CMD 하나에서 실행한다. 기존 Galaxybook의 hybrid-windows DB/볼륨/기준금/요청 이력을 유지한다. 초기화하지 않는다.

## 실행 순서

1. ZIP을 새 폴더에 풀고 폴더 주소창에 cmd 입력. 아래 빌드가 모두 Healthy가 될 때까지 기다린다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

2. 교체된 API 컨테이너의 RAM 키를 등록한다. DEMO API Key와 Secret을 숨김 입력하며 채팅에 보내지 않는다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.demo_key_session --register
```

3. 아래 명령을 한 번만 실행한다. 신규 주문을 제출하지 않는다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.check_demo_empty_worker_recovery --enable-fault-injection --operation-id b4cceb33-35bd-4430-b5be-65a49924d9ba
```

WORKER_STARTED → WORKER_READY_KILLING → WORKER_SIGKILL_VERIFIED → WAIT_NATURAL_LEASE_EXPIRY → NEW_LEADER_RECONCILED → 최종 결과를 기다린다. WAIT_NATURAL_LEASE_EXPIRY에서 약60초 대기는 정상이다. 전체 실행에는 사전 조회/연결/재대조/정리가 추가된다. 창을 닫거나 API·DB를 재시작하지 않는다. 직접 kill 명령이나 다른 CMD 작업을 실행할 필요가 없다.

오류가 나면 재실행하지 않고 최종 출력 전체를 공유한다. 같은 UUID는 기록이 있으면 프로세스 생성 전에 차단한다.

## 실제로 검사하는 범위

주문 없는 NORMAL/paused ISOLATED BTCUSDT 2x 계좌가 필요하다. 부모와 자식 모두 기존 ObservationClient를 사용하며 일반 SDK request의 GET 이외 메서드는 SDK 전송 전에 거부한다. 개인 WSS의 listenKey 생성/삭제는 별도 기존 경로로 허용한다. DB 감사·리더 변경은 있으므로 DB 읽기 전용 검증은 아니다.

부모는 API 컨테이너 안에 별도 Python 관찰 자식 프로세스를 띄운다. 자식이 실제 WSS 연결/REST 재대조를 완료하고 고유 작업 marker를 기록하면, 기존 활성 lease에 경쟁 리더가 들어오지 못하는지 검사한다. 해당 자식 Popen 핸들에 SIGKILL을 한 번 보내고 종료 코드 -9를 확인한다. API/DB 컨테이너나 Windows 호스트 프로세스를 종료하지 않는다.

기존60초 lease가 자연 만료될 때까지 기다린다. lease_until/fence를 직접 수정하거나 만료를 앞당기지 않는다. 새 리더를 획득하면 fence가 정확히1 증가하고, 재대조 이전 stream/reconciled false 및 진입 게이트 거부를 검사한다. 이전 lease의 store.change 효과 함수와 renew가 실행되지 못하는지도 확인한다. 이후 전체 REST와 실제 WSS 준비를 확인한 뒤 세션을 정상 종료하고 새 lease를 release한다. 마지막은 NORMAL/paused/stream false/reconciled false/owner 없음이다.

자식은 같은 컨테이너의 기존0700/0600 private tmpfs 키 캐시를 읽는다. 새 키 파일, 키 argv, 추가 키 환경변수 전달, Docker 소켓/포트/마운트를 만들지 않는다. 부모가 사라져도 자식 관찰 대기는180초로 제한된다. 실패 시 자기 자식을 종료하고 알고 있는 자기 lease만 자연 만료 후 회수·해제하려 시도한다. 다른 리더의 소유권은 건드리지 않는다. 실패/정리 불확실은 통과가 아니다.

성공 결과: EMPTY_WORKER_SIGKILL_FENCED_RECOVERY_VERIFIED. 강제 종료·경쟁 리더 차단·이전 리더 효과/갱신 차단·재대조 전 게이트 차단·새 리더 준비·최종 release·이력/기준 보존을 모두 확인해야 한다. 신규 진입 POST는0이다. worker_exit_code -9는 의도한 강제 종료 결과다.

이 검증은 빈 계좌의 관찰 프로세스 강제 종료 범위다. 주문/포지션을 보유한 주문 Worker 중단·복구, 전체 API/DB/호스트 중단, 실제 부분체결 보호, 장기 무인 운용 또는 Phase4 전체 통과를 뜻하지 않는다. LIVE/Phase5 설정을 바꾸지 않는다.
