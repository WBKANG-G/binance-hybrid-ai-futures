# Phase 4 Windows 격리(Isolated) 모드 검증

현재 진행할 작업은 주문 없는 DEMO 인증 조회입니다. 실제 주문/Phase 5/실거래를 시작하지 않습니다.
Binance DEMO의 BTCUSDT를 Isolated·2배로 유지하고, 포지션/미체결 주문이 없는지 확인하세요.
Cross로 되돌리지 마세요. 이 프로그램은 계좌 마진 모드나 레버리지를 자동 변경하지 않습니다.

## 설치

1. Docker Desktop을 켭니다.
2. 새 ZIP을 모두 추출한 뒤 windows-demo-isolated 폴더에서 infra가 보이는 위치로 들어갑니다.
3. 탐색기 주소창에 cmd를 입력하고 Enter를 누릅니다.
4. 아래 명령으로 갱신합니다. 기존 hybrid-windows 프로젝트와 DB 볼륨을 그대로 사용합니다.

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

기존 폴더/DB/볼륨을 삭제하지 마세요. down -v를 실행하지 마세요.
개인 .env/키 파일은 새 폴더에 복사하지 마세요. API/웹은 기존 PAPER·실거래 비활성 설정을 유지합니다.

## Healthy 확인 후, 새 격리 전용 원장으로 첫 인증 조회

기존 Cross 원장과 섞지 않도록 DEMO-WINDOWS-ISOLATED-BTC-V1이라는 새 내부 원장을 만듭니다.
동일한 실제 DEMO 계좌를 읽지만 기존 DB 기록은 삭제하거나 바꾸지 않습니다.
아래 --initialize 명령은 처음 한 번만 사용합니다. 오류가 나면 반복하지 말고 정제 결과만 공유하세요.

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -c "import getpass,os,runpy,sys; os.environ['LIVE_TRADING_ENABLED']='false'; os.environ['BINANCE_DEMO_API_KEY']=getpass.getpass('DEMO API Key (hidden): '); os.environ['BINANCE_DEMO_API_SECRET']=getpass.getpass('DEMO Secret (hidden): '); sys.argv=['scripts.run_demo','--config','configs/demo-isolated.example.yaml','--account','DEMO-WINDOWS-ISOLATED-BTC-V1','--initialize']; runpy.run_module('scripts.run_demo',run_name='__main__')"
```

보관한 DEMO key/secret을 차례대로 입력하고 Enter를 누릅니다. 입력 문자가 표시되지 않는 것이 정상입니다.
숨김 입력 불가 경고가 나오면 Ctrl+C로 중단하고 키를 입력하지 마세요.
키 값/서명/listenKey를 채팅/스크린샷으로 보내지 마세요.
이 명령에는 --enable-demo, --watch, --signal이 없습니다. 주문을 활성화하지 않습니다.

확인할 결과: preflight=READ_ONLY, account_reconciled=true, risk_state=NORMAL,
margin_mode=ISOLATED, configured_symbols=[BTCUSDT], verified_symbol_settings의 margin_mode=ISOLATED, leverage=2,
orders_enabled=false, live_trading_enabled=false.
private_stream_ready=false는 이 읽기 전용 단계에서는 정상입니다.

자동 증거금 추가 설정은 API에서 false임을 확인해야 합니다. 누락/true/알 수 없는 값이면 거부합니다.
이 경우 설정을 추측하거나 검사 코드를 완화하지 않고 정제 진단을 먼저 확인합니다.
첫 조회 실패로 원장만 만들어졌다면 --initialize를 반복하지 않습니다.
후속 조회는 동일 account/config로 --initialize 없이 실행합니다.

## 구현된 안전 경계

- ISOLATED는 명시적인 BTCUSDT 범위만 사용합니다. 다른 종목의 Cross 설정은 자동 변경하지 않습니다.
- 범위 밖 실제 포지션/미체결 일반·Algo 주문이 있으면 거부합니다.
- 격리 모드/종목 범위를 원장에 고정하며 기존 Cross 원장의 암묵적 전환을 거부합니다.
- totalWalletBalance에 isolatedWallet을 더하지 않습니다. availableBalance에 격리 담보를 더하지 않습니다.
- 마진 사용량은 계좌 initial margin과 격리 담보 합계 중 큰 값으로 보수적으로 계산합니다.
- 격리 포지션은 양수의 담보/유효 liquidationPrice 증거를 요구합니다. 손절이 청산가격 너머라면 신규 진입을 막고 위험 축소 경로로 연결합니다. 손절 체결 보장은 아닙니다.
- 주문 전 fresh 계좌 snapshot으로 mode/leverage/잔고/담보 fingerprint 변화를 검사합니다.
- SL/TP와 일반 주문 ID 분리 및 실제 flat 이후 정리 규칙을 유지합니다.
- Algo 취소는 원래 client ID로 종목을 조회해 범위를 확인한 뒤 수행합니다.
- 문제 종목 규칙의 완화/하드코딩 우회, LIVE 활성화, 마진 모드 자동 변경은 하지 않습니다.

## 미완료 항목

실제 격리 DEMO 인증은 Windows에서 후속 검증해야 합니다.
실제 격리 체결·Algo 지원·SL/TP·청산가격 필드·부분체결·취소·장애 복구는 아직 거래소에서 검증하지 않았습니다.
이 배포만으로 Phase 4 완료나 실거래 안전성을 주장하지 않습니다.
공식 최신 문서 3개(account V3/positionRisk V3/symbolConfig)는 이 클라우드에서 CONNECT 403으로 조회 불가했습니다.
구현의 필드/가정은 합성 계약 테스트 결과이며 실제 API 응답에서 재확인이 필요합니다.

## 다음 순서

위 읽기 전용 결과를 먼저 공유하고, 격리 계좌 설정을 확인한 후 비공개 연결을 다시 검증합니다.
주문 가격/수량/손절/익절은 아직 입력하지 마세요. 현재 시세/metadata/계좌 상태에 맞춘 별도 검증 신호가 필요합니다.

# 격리 모드 수정 검증 기록

Python 전체: 301 passed / 0 failed / 0 skipped (24.21초).
명령: TEST_DATABASE_URL=<local dedicated PostgreSQL> python -m pytest -q -p no:cacheprovider
실제 PostgreSQL 격리 schema 및 localhost WebSocket 사용. 거래소 응답/자격증명은 합성 데이터.
Ruff check/format 및 변경된 5개 source 파일 mypy 통과.
프런트엔드/의존성 lockfile/MASTER_SPEC/AGENTS/실거래 위험 설정은 변경 없음.
초기 실행에서 설정 schema 재생성 누락을 발견해 기존 schema 생성 방식으로 수정했습니다.
격리 장애 테스트 중 Algo 취소 종목 확인 경로 오류를 발견해 수정하고 전체 재검증했습니다.
Binance 실계좌/DEMO 인증/주문을 이 클라우드에서 실행하지 않았습니다.
기존 Windows Cross 기반 인증/연결 성공은 이전 사용자가 제공한 화면 증거이며 새 격리 검증 성공을 의미하지 않습니다.
최신 공식 문서 3개 조회는 proxy CONNECT 403으로 차단되었습니다. 실제 응답 계약 후속 검증 필요.
실거래/Phase 5 미착수, main/기존 ZIP 변경 없음.
