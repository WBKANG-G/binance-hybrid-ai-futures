# Phase 4 반대 호가 지정가 DEMO 검증

이전 작업은 미체결·취소·flat으로 끝났습니다. 그 작업 ID는 그대로 보존하고 다른 진입 방식으로 덮어쓰지 않습니다.
이번 명령은 조회 전용이 아닙니다. 현재 매도 1호가를 가격 상한으로 하는 최소 수량 LONG 지정가 1건을 DEMO에 시도합니다.
시장가 신규 진입/자동 가격 추격/재호가/추가 진입은 없습니다. 호가 이동·유동성 부족으로 미체결될 수도 있습니다.

## 설치

Docker Desktop을 켜고 새 ZIP을 모두 추출합니다. windows-demo-opposite 폴더에서 infra가 보이는 위치로 들어가
탐색기 주소창에 cmd를 입력합니다. 아래 명령은 이미지 갱신만 하며 주문을 보내지 않습니다.

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

기존 hybrid-windows DB/원장/폴더를 삭제하지 마세요. down -v를 사용하지 마세요.
실제 .env/개인 키 파일을 새 폴더에 복사하지 않습니다. API/웹은 PAPER·주문 비활성을 유지합니다.
Binance DEMO는 BTCUSDT Isolated·2배, 포지션/미체결 0개, 다른 봇/수동 거래 없는 상태를 유지합니다.

## Healthy 확인 후 실제 DEMO 검증

아래는 이전 미체결과 구분된 새 작업입니다. 새 ID는 DEMO_OPPOSITE_OPERATION_ID.txt에 저장되어 있습니다.
실행 전에 기존 미체결 작업이 FINISHED_FLAT으로 끝났는지 확인합니다. 원장을 초기화하지 않습니다.

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -c "import getpass,os,runpy,sys; os.environ['LIVE_TRADING_ENABLED']='false'; os.environ['BINANCE_DEMO_API_KEY']=getpass.getpass('DEMO API Key (hidden): '); os.environ['BINANCE_DEMO_API_SECRET']=getpass.getpass('DEMO Secret (hidden): '); sys.argv=['scripts.run_demo','--config','configs/demo-isolated.example.yaml','--account','DEMO-WINDOWS-ISOLATED-BTC-V1','--enable-demo','--validate-minimum','LONG','--entry-style','OPPOSITE_QUOTE','--operation-id','892f0bd6-66ba-4534-80b3-074039f676fb']; runpy.run_module('scripts.run_demo',run_name='__main__')"
```

DEMO API Key/Secret을 차례대로 숨김 입력에 넣고 Enter를 누릅니다. 글자가 보이지 않는 것이 정상입니다.
숨김 입력 불가 경고가 나오면 Ctrl+C로 중단하고 키를 입력하지 마세요.
키/Secret/서명/listenKey를 채팅이나 스크린샷으로 보내지 마세요.
입력 후 조회·보호 준비·약 30초 관찰·종료 대조를 기다립니다. 창을 닫거나 절전/수동 거래하지 마세요.
오류나 UNRESOLVED_DO_NOT_REPEAT이면 새 ID로 반복하지 말고 정제 결과만 공유합니다.
같은 작업 ID는 기존 작업을 복구하며 추가 진입을 생성하지 않습니다. 가격 방식 변경도 거부합니다.

## 변경된 가격 방식

- 기본 PASSIVE 동작은 유지됩니다. OPPOSITE_QUOTE는 명시적 DEMO 검증 또는 조회 전용 미리보기에서만 선택합니다.
- LONG은 현재 ask, SHORT는 현재 bid를 그대로 지정가로 사용합니다.
- 양쪽 호가가 거래소 tick 규칙과 맞아야 합니다. 어긋난 호가에 가격 여유를 더해 주문하지 않습니다.
- 가격·수량을 한 번 고정하고 전송 전 최신 호가/mark/계좌 상태를 재검사합니다. 실제 주문 가격을 자동으로 올려 따라가지 않습니다.
- 최소 유효 수량·수량 상한·제출 명목금액 100 USDT 한도·Isolated·2배·LIVE false를 유지합니다.
- 손절/익절 가정 0.5%/1%와 생성·조회 검증, 미체결 잔량 취소, 필요시 reduce-only 종료 및 실제 flat 확인 절차는 이전과 같습니다.
- RESERVED_NOT_YET_SUBMITTED의 entry_style=OPPOSITE_QUOTE를 확인하세요.

## 결과 구분

FINISHED_FLAT은 마지막 대조에서 포지션·활성 일반 주문·활성 Algo 주문이 없는 상태를 확인했다는 뜻입니다.
entry_outcome=UNFILLED이면 체결/보호 주문 검증은 미완료입니다. PARTIAL_FILL과 FULL_FILL도 구분합니다.
filled_protection_and_flat_verified=true는 실제 체결·SL/TP 주문 상태 조회·flat 종료를 확인했다는 뜻이며
SL/TP의 실제 trigger 체결이나 Phase 4 전체 완료를 의미하지 않습니다.
EMERGENCY 잠금이 남으면 자동 해제하지 않습니다. 새 작업으로 우회하지 않습니다.

실제 Binance 실행 전에는 체결 성공/보호 기능 지원을 보장하지 않습니다. 실거래와 Phase 5는 시작하지 않습니다.

# 반대 호가 지정가 DEMO 검증 준비 기록

Python 전체: 361 passed / 0 failed / 0 skipped (60.27초).
실제 PostgreSQL 격리 schema와 localhost WebSocket, 합성 거래소 응답/자격증명을 사용했습니다.
전체 mypy strict 62 source 통과. 변경 파일 Ruff check/format 통과.

추가 검증:
- 읽기 전용 LONG=ask / SHORT=bid, 정확한 tick 가격 사용, tick 불일치 거부.
- LONG/SHORT에서 LIMIT 1회·수량 상한 유지·미체결 취소·체결 후 SL/TP 확인 및 실제 flat 정리.
- 같은 작업 ID의 방식 변경 거부, 동일 방식 복구 시 가격 추격/추가 진입 없음.
- 일반 실행에 명시적 반대 호가 옵션을 잘못 혼용하면 인증 전 거부.
- 거래소 필드 누락/숫자 형식 오류를 DemoFailure로 정제해 기존 최소 positionRisk 위험 축소 경로로 연결.
  기존 필터 불량 테스트도 동일한 필드 거부와 주문 미전송을 유지하며 예외 종류만 정제된 도메인 오류로 갱신했습니다.
- 체결 후 격리 담보 필드 누락을 주입해 reduce-only 종료 시도가 실행됨을 확인했습니다.

기본 PASSIVE·TTL·금액/수량 한도·LIVE false·PAPER 기본 API·계좌 마진 모드 자동 변경 금지는 유지합니다.
main/이전 ZIP/의존성 lockfile/MASTER_SPEC/AGENTS/실거래 위험 설정은 변경하지 않았습니다.
프런트엔드는 변경/재검증하지 않았습니다.

실제 Windows Binance 관측: 이전 작업은 entry_state=CANCELED / entry_outcome=UNFILLED / FINISHED_FLAT,
수량 0.0007 BTC, stop/target 미검증, session_error=false였습니다(사용자 화면 증거).
새 OPPOSITE_QUOTE 실제 Binance 실행은 아직 하지 않았습니다. 체결이나 보호 주문 지원을 보장하지 않습니다.
Phase 4 전체 완료를 선언하지 않으며 실거래와 Phase 5는 시작하지 않았습니다.
