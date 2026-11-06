# Windows Docker 독립 장애 복구 검사

현재 DEMO 계좌는 NORMAL/일시정지로 유지한다. 이번에는 별도 hybrid-phase4-fault-check 프로젝트의 테스트 DB와 합성 거래소만 사용한다. 운영 hybrid-windows 컨테이너와 DB·키 RAM·export 볼륨은 연결하지 않는다. 실제 Binance/Naver 요청이나 실제 주문은 없다.

## 1. 새 폴더 준비

ZIP을 새 windows-demo-isolated-fault-check 폴더에 풀고 해당 폴더 탐색기 주소창에서 cmd를 실행한다. 기존 실행 폴더에 덮어쓰지 않는다. 아래 전용 프로젝트/Compose 명령을 그대로 사용한다. 운영용 프로젝트를 재빌드할 단계가 아니다.

이미지에는 uv.lock과 같은 버전의32개 검증용 의존성을 포함했고1779개 파일 해시와 아카이브 SHA256을 기록했다. Python3.12/Linux AMD64 테스트 이미지다. 베이스 Python/PostgreSQL 이미지가 있으면 패키지 저장소에 접속하지 않고 빌드한다. 기존 운영 이미지의 의존성/설정은 바뀌지 않는다.

## 2. 빌드하고 검사 실행

```bat
docker compose -p hybrid-phase4-fault-check -f infra/docker-compose.phase4-fault-check.yml up --build --abort-on-container-exit --exit-code-from checks
```

빌드가 끝난 뒤 worker의 원래60초 리스가 자연 만료되기를 기다리는 구간이 있다. 출력이 잠시 없어도 창을 유지한다. API 키나 비밀번호 입력은 없다. 테스트 전용 DB의 관리자 연결 종료 FATAL 로그는 의도된 장애 주입이며, 최종 checks 결과로 판정한다.

검사13개가 모두 통과하고 실패/건너뛰기0일 때만 fault_check ISOLATED_SYNTHETIC_FAULT_CHECK_PASSED가 나온다. 실제 child SIGKILL과 원래60초 대기, SLTP 유지/펜스 인계/중복 없음 및 DB 접속 종료 지점을 검사한다. 클록 전진이나 기존 테스트 조건 변경으로 시간을 줄이지 않는다.

## 3. 결과만 짧게 확인

```bat
docker compose -p hybrid-phase4-fault-check -f infra/docker-compose.phase4-fault-check.yml logs --tail 5 checks
```

13 passed, PROTECTED_WORKER_RESULT_JSON 및 최종 fault_check JSON이 보이도록 결과를 공유한다. NOT_ACCEPTED/BLOCKED/빌드 오류이면 그대로 공유하고 실제 계좌에 장애를 주거나 기존 앱을 복구/초기화하지 않는다. 이번 결과는 합성 계좌·전용 DB 범위이고 실제 Binance 보호 포지션 장애 수락은 아니다.

## 검사 뒤 전용 자원 정리

결과를 보존한 뒤 아래 명령으로 이번 테스트 컨테이너와 내부 네트워크만 제거할 수 있다. 운영 프로젝트와 키/DB 볼륨에는 적용하지 않는다.

```bat
docker compose -p hybrid-phase4-fault-check -f infra/docker-compose.phase4-fault-check.yml down
```

테스트 DB는 tmpfs라 정지 후 시험 데이터가 유지되지 않는다. 기존 운영 DB는 별도 프로젝트/볼륨이며 이 검사에 마운트하지 않는다. 일시정지 해제나 Phase4 전체 완료/LIVE/Phase5 전환은 없다.
