# Phase 4 Windows DEMO 비공개 연결 검증 준비

실거래 및 Phase 5는 금지합니다. 이 배포는 기존 preflight 수정에 이어 종목 격리와 제한된 실시간 계좌 연결 검사를 추가합니다.

## 변경과 안전 경계

- 잘못된 규칙은 완화하지 않고 excluded_symbols로 제외합니다. 제외 종목은 진입 예약/제출할 수 없습니다.
- 제외 종목의 실제 포지션/활성 일반 주문/활성 Algo 주문/내부 미결 의도가 있으면 계좌 대조를 거부합니다.
- --check-private는 먼저 쓰기 비활성 상태로 대조하고 빈 NORMAL 계좌인지 검사한 뒤 session을 시작합니다.
- 20초간 연결을 관찰합니다. --watch/--signal/--command/--resume-entries/--initialize와 조합할 수 없습니다.
- listenKey 생성/종료를 위해 --enable-demo를 명시하지만 주문 신호는 전달하지 않습니다.
- 다른 봇이나 수동거래가 같은 DEMO 계좌를 사용해서는 안 됩니다. 기존 보호가 필요한 포지션이 있는 계좌에 사용하지 마세요.
- timeout 종료 시 session cleanup/진입 정지/stream 무효화를 시도합니다. 실패를 숨겨 성공으로 판단하면 안 됩니다.
- CONNECTED는 해당 시점의 handshake/REST 대조 성공, OBSERVATION_FINISHED는 관찰 종료입니다. 25분 session 갱신, 실제 체결/SL/TP/복구는 미검증입니다.

## 설치

Docker Desktop을 켜고 새 ZIP을 모두 추출합니다. infra 폴더가 보이는 위치에서 탐색기 주소창에 cmd를 입력합니다.

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

같은 프로젝트 이름을 유지하므로 기존 hybrid-windows_postgres_data를 재사용합니다.
기존 폴더/DB/볼륨을 삭제하지 마세요. 실제 .env/개인 키 파일은 새 폴더로 복사하지 마세요.
새 빌드 Healthy 결과를 확인한 다음 아래 연결 검사로 진행합니다.

## 키 숨김 입력과 20초 연결 검사

아래 기존 내부 원장은 이 사용자의 선행 preflight에서 인증/대조 NORMAL이 확인된 원장입니다. --initialize를 다시 사용하지 않습니다.

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -c "import getpass,os,runpy,sys; os.environ['LIVE_TRADING_ENABLED']='false'; os.environ['BINANCE_DEMO_API_KEY']=getpass.getpass('DEMO API Key (hidden): '); os.environ['BINANCE_DEMO_API_SECRET']=getpass.getpass('DEMO Secret (hidden): '); sys.argv=['scripts.run_demo','--config','configs/demo.example.yaml','--account','DEMO-WINDOWS-6d9329cae37d40ada14c0169d38dd07e','--enable-demo','--check-private']; runpy.run_module('scripts.run_demo',run_name='__main__')"
```

보관한 DEMO key/secret을 차례대로 입력하고 Enter를 누릅니다. 화면에 입력 문자가 보이지 않는 것이 정상입니다.
숨김 입력을 사용할 수 없다는 경고가 뜨면 Ctrl+C로 중단하고 키를 입력하지 마세요.
키 값/서명/listenKey를 채팅이나 스크린샷으로 보내지 마세요. 오류가 나오면 자동 재실행하지 않습니다.
완료 문구 및 오류 결과만 공유합니다. 종료 후 관찰 세션은 계속 실행되는 worker가 아닙니다.

## 검증 결과

- 관련 Python 테스트 73 passed (9.03초): 실제 PostgreSQL 격리 schema + 합성 거래소 + 실제 localhost WS 포함.
- 이후 추가한 제외 종목 진입 거부 테스트 포함 해당 파일 9 passed (0.26초). 전체 서로 다른 검사 74개 통과.
- Ruff check/format, mypy 통과.
- 실제 Binance private WSS는 클라우드에서 인증 실행하지 않았습니다. Windows에서 후속 확인 필요.
- 기존 Windows 공개 REST/WSS PASS 및 읽기 전용 DEMO 인증 NORMAL은 사용자의 선행 화면 증거입니다.
- main/기존 ZIP은 변경하지 않습니다. 실제 주문은 이 변경 작업에서 전송하지 않았습니다.
