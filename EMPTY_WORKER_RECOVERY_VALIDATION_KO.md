# 빈 계좌 관찰 프로세스 강제 종료·리더 복구 검증 기록

부모 ZIP SHA256: 067562df57c011aadb6abda07850dda0942f269dbdf19e5df7c693839b19c31e.
기존 manifest 파일349개를 바이트 그대로 보존한다. services/execution/demo_empty_worker_recovery.py, scripts/check_demo_empty_worker_recovery.py, 새 통합/단위 테스트, 안내/검증 문서 및 최근 실제 WSS 통과 증거를 추가한다.

## 구현과 제한

신규 주문 없는 계좌만 허용한다. 기존 ObservationClient의 GET-only 일반 request 거부를 부모/자식 모두 사용하고, listenKey 수명 관리만 기존 SDK 경로로 수행한다. DB 리더·감사 기록은 변경한다. SDK/리스크/주문/보호/청산/키 보관/기간 기준/Compose/exports/기존 테스트는 수정하지 않는다.

자식은 sys.executable -m scripts.check_demo_empty_worker_recovery의 내부 worker 경로다. argv는 작업 및 소유자 UUID와 플래그뿐이고, 키를 argv나 별도 환경변수에 넣지 않는다. 같은 API 컨테이너의 private tmpfs 캐시를 그대로 읽는다. stdin/stdout/stderr는 DEVNULL이며 raw 오류·URL·payload를 전달하지 않는다. 고유 owner UUID와 현재 fence의 DB marker/준비 상태를 확인한 뒤 해당 Popen 핸들만 kill한다. 종료 -9를 확인한다. 자식은 원래 session을 사용하고 부모 소실 시 관찰 대기는180초다.

활성 lease 동안 competing acquire가 거부돼야 한다. kill 뒤 현재 소유자와 fence가 바뀌지 않았는지 읽기 전용 트랜잭션으로 확인하며 자연 만료를 기본75초 대기 예산으로 관찰한다(진행 중인 조회·정리 시간은 추가될 수 있다). 기존60초 lease 값을 직접 변경하지 않는다. 새로운 acquire가 fence를 정확히1 올려야 한다. acquire가 stream/reconciled를 false로 만드는 기존 동작과 진입 게이트 차단을 검사한다. stale lease의 effect callback은 절대 호출되면 안 되고 renew도 거부돼야 한다. 기존 SDK clock 동기화, metadata/REST 대조, WSS session 준비를 통과해야 한다. 세션 정리는 이전 수정의 drain_session으로 완료까지 기다린다.

최종 기존 이력/기준값 보존·NORMAL·paused·stream/reconciled false·leader release를 모두 요구한다. fenced 감사 marker는 EMPTY_WORKER_CHECKED_RELEASE_PENDING으로 기록하며, release 성공은 이후 소유자 조회와 최종 출력으로 별도 검증한다. marker 자체가 최종 release 성공을 뜻하지 않는다. 어떤 실패/미확인 값도 통과로 올리지 않는다. 다른 소유자가 나타나면 takeover를 중단하고 소유권을 덮어쓰지 않는다.

## 로컬 검증

집중 테스트: 18 passed in 6.87s. Ruff check/format 및 mypy98소스 통과.
전체 테스트: 1095 passed in 224.20s (0:03:44). Ruff check/format 통과, mypy98소스 통과.

통합 테스트는 실제 OS 자식 Python 프로세스를 생성해 SIGKILL(-9)하고 실제 PostgreSQL 격리 schema에서 경쟁 리더/만료/새 fence/이전 효과·renew 거부/재대조를 검사한다. WSS는 localhost 서버이며 거래소는 synthetic signed SDK 응답이다. 테스트의 부모/자식 시각은 fixture 시각이고, kill 확인 후 테스트 시계를61초 진행해 기존60초 lease 만료를 결정적으로 검사한다. 실제60초 벽시계 대기를 클라우드에서 수행한 것으로 주장하지 않는다. Windows 실행은 실제 UTC 시각의 자연 만료를 기다린다.

미만료 상태의 timeout은 기존소유자/fence 유지, 새 acquire 없음으로 검사한다. 추가로 이전 효과 함수가 실행되는 결함, 재대조 전 게이트 검사 실패, 재연결 실패, 입출금 변화의 이력 보존 실패, 기존 UUID/실제 포지션이 있는 경우 시작 차단, release 실패의 거부, CLI 플래그/LIVE 거부 및 키 없는 argv/추가 환경변수 부재를 검사했다.

클라우드에서는 인증된 Binance 주문·WSS를 호출하지 않았다. 실제 Windows empty worker 검증은 아직 실행 전이다. 기존 실제 WSS 단절 통과 debe90bc-1195-4e1b-92ef-22f41229c64e는 별도 증거 문서로 포함했다. 실제 부분체결 보호와 보유 주문/포지션 Worker 중단·복구, 전체 호스트/DB 장애, 장기 운영은 미완료이며 Phase4 전체/LIVE/Phase5 완료로 확대하지 않는다.
