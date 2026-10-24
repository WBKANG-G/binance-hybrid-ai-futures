# 종료 시 진행 중인 DB 처리 대기와 기존 청산 확인

이번 사용자 거래소 조회는 포지션/일반 미체결/조건부 미체결 모두0, 기존 청산 주문 FILLED/0.0007 BTC입니다. 로컬만 EXIT_PENDING/ACKNOWLEDGED에 남아 있습니다. 이번 단계는 새 주문 없이 기존 종료를 대조하고 로컬 기록을 반영합니다. 자동 종료가 최초 실행에서 성공한 것으로 과거 기록을 바꾸지 않습니다.

## 1. 기존 Galaxybook PC의 새 폴더에 설치

ZIP을 풀고 windows-demo-worker-drain-fix 폴더를 열어 주소창에 cmd를 입력합니다. CMD 경로가 새 폴더로 끝나는지 확인하세요.

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

기존 hybrid-windows 프로젝트와 PostgreSQL/export 볼륨, 일별/주별 기준·고점·복구 기록을 유지합니다. 초기화, down -v, export 요청, 과거 일별 기준 재적용을 하지 않습니다.

## 2. 기존 청산만 확인

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -c "import getpass,os,runpy,sys; from services.execution.demo_stream import settled_call; os.environ['LIVE_TRADING_ENABLED']='false'; os.environ['BINANCE_DEMO_API_KEY']=getpass.getpass('DEMO API Key (hidden): '); os.environ['BINANCE_DEMO_API_SECRET']=getpass.getpass('DEMO Secret (hidden): '); sys.argv=['scripts.run_demo','--config','configs/demo-isolated.example.yaml','--account','DEMO-WINDOWS-ISOLATED-BTC-V1','--enable-demo','--confirm-flat']; runpy.run_module('scripts.run_demo',run_name='__main__')"
```

목표는 completion_confirmation=VERIFIED_FLAT입니다. 마지막 결과를 공유하세요. API 키/Secret은 숨김 입력하고 채팅에 보내지 않습니다. 이 명령에는 신규 진입이나 청산 POST가 없습니다. 기존 원래 ID의 체결·현재 포지션·미체결을 확인하며, 실제로 청산이 입증된 경우에만 기존 잔여 보호를 취소할 수 있습니다. 로컬 상태와 대조 감사 기록은 갱신됩니다. 포지션이 남아 있거나 종료가 불명확하면 UNKNOWN으로 끝내고 청산 POST를 새로 제출하지 않습니다. EMERGENCY/paused를 해제하거나 주문을 재개하지 않습니다.

## 수정 내용

asyncio의 to_thread를 기다리는 비동기 작업을 취소해도 실행 중인 스레드는 멈추지 않습니다. 기존 종료 경로는 그 스레드가 DB 행 잠금을 잡은 상태에서 다음 일시정지/정리를 시작할 수 있었습니다. 수정판은 진행 중인 스레드가 완료 또는 실패할 때까지 기다린 다음 취소를 전달하고 finally 정리를 시작합니다. 반복 취소 요청에도 스레드를 남기지 않습니다. 네트워크/lease/행 잠금 시간 제한은 늘리지 않고 그대로 둡니다.

DB 오류는 SQL·파라미터 원문 대신 DATABASE_LOCK_TIMEOUT/DEADLOCK/CONNECTION_FAILED/OTHER_DATABASE_FAILURE 같은 허용 분류만 출력합니다. validation 출력의 completion_database_failure도 확인할 수 있습니다.

이전 조회 지연·시각 정밀도·일별 기준 관련 수정은 포함되어 있습니다. 실제 수정판의 새로운 자동 진입·보호·청산 검증은 기존 종료 확인 후 별도로 진행합니다. LIVE/Phase5를 시작하지 않습니다.
