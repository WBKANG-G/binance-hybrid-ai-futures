# 빈 계좌 DB 서버 재시작·장부 보존 점검

신규 진입·청산·보호 주문을 보내지 않는 Phase4 점검입니다. 범위는 작업이 실행 중이지 않은 빈 계좌에서 PostgreSQL 컨테이너를 정상 재시작한 뒤 장부 이력·기준금·회계·일시정지와 빈 계좌 상태가 보존되는지 확인하는 것입니다. 실행 중 DB 장애, 전원 손실, 호스트 재부팅, 포지션 worker 장애를 재현하는 시험은 아닙니다.

점검 도구 자체는 Docker를 제어하지 않습니다. Docker 소켓/권한/의존성을 추가하지 않습니다. before/after 감사 표식만 기존 DB에 기록하며 두 단계가 끝나면 리더를 반환합니다. 키는 기존 private tmpfs RAM을 사용합니다.

## 1. 빌드와 키 등록

ZIP을 새 windows-demo-empty-db-restart 폴더에 풉니다. 기존 폴더에 덮어쓰지 않습니다. 새 폴더의 탐색기 주소창에 cmd를 입력합니다. 기존 hybrid-windows 프로젝트와 볼륨을 유지합니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

모든 컨테이너 Healthy 확인 뒤 숨김 입력으로 키를 등록합니다. 키/Secret을 채팅에 보내지 않습니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.demo_key_session --register
```

## 2. 재시작 전 준비만 먼저 실행

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.check_demo_empty_db_restart --prepare --operation-id 98e59d82-c594-4ceb-936e-fa1a81ee83eb
```

PREPARED_EMPTY_NO_ORDERS, exchange_empty_verified true, history_and_baselines_preserved true, risk_state NORMAL, paused true, leader_released true를 확인해야 합니다. 먼저 전체 결과를 캡처해 공유합니다. 이 준비 결과를 확인하기 전에는 DB를 재시작하지 않습니다. BLOCKED면 준비를 반복하거나 UUID를 바꾸지 않습니다.

두 번의 독립 GET 스냅샷으로 포지션/활성 일반·Algo 주문/마진 사용이 없는지 확인하고, 로컬 장부 전체의 비교 해시와 원격 상태 해시, PostgreSQL 시작 시각, 펜스를 저장합니다. stream_ready/reconciled와 이 점검 자체의 두 표식만 로컬 비교에서 제외합니다. 그 밖의 장부 필드, 기존 운영 표식, UNKNOWN/NOT_SENT 증거, 기준금·고점·회계는 모두 비교합니다. 기준금 날짜를 새로 맞추거나 진입 준비가 됐다고 승인하는 도구는 아닙니다.

## 3. 준비 결과 확인 후 DB만 재시작

준비 결과를 확인한 뒤 아래 순서로 한 줄씩 실행합니다. 단계 사이에 다른 DEMO 명령·봇·수동 거래·입출금을 실행하지 않습니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml restart db
```

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --wait
```

모두 Healthy가 된 뒤 같은 UUID로 검증합니다.

```bat
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.check_demo_empty_db_restart --verify --operation-id 98e59d82-c594-4ceb-936e-fa1a81ee83eb
```

정상 결과는 EMPTY_DB_RESTART_HISTORY_PRESERVED_VERIFIED, postgres_restart_observed true, history_and_baselines_preserved true, exchange_empty_verified true, leader_released true입니다. 실제 PostgreSQL 시작 시각이 뒤로 바뀌고, 펜스가 prepare 때보다 정확히1 증가해야 합니다. 리스 만료를 강제하거나 펜스를 초기화하지 않습니다.

BLOCKED이면 다시 시작하거나 같은 prepare/verify를 반복하지 말고 전체 결과를 공유합니다. 다른 작업이 개입하거나 장부/원격 상태가 바뀌면 재시작이 정상이어도 통과하지 않습니다. 준비/검증이 끝난 UUID는 재사용하지 않습니다.

## 유지할 것

현재 NORMAL/paused를 유지합니다. 신규 진입과 LIVE는 비활성입니다. down -v, 볼륨 삭제, DB 초기화, 기준금/고점/UNKNOWN 재설정, exports 생성은 없습니다. API를 의도적으로 재시작하지 않습니다. 키 캐시가 없어졌다면 키 값을 출력하지 말고 오류 결과를 공유합니다. 이전 SL 로컬 실패 통과·복구 승인, 응답 유실 미통과 및 종료 확인 수정 통과 기록은 그대로 보존합니다.
