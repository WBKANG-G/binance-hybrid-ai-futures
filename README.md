# Phase 4 주문 없는 DEMO 주문조건 미리보기

이 단계는 주문을 보내지 않습니다. 실제 거래나 실행 가능한 매매 신호를 만들지 않습니다.
Binance DEMO의 BTCUSDT Isolated·2배 설정을 유지하고, 다른 봇/수동 주문 없이 빈 계좌에서 진행합니다.

## 1. 새 버전 설치

Docker Desktop을 켭니다. 새 ZIP을 모두 추출하고 windows-demo-preview 폴더에서 infra가 보이는 위치로 들어갑니다.
탐색기 주소창에 cmd를 입력하고 Enter를 누른 뒤 아래 명령을 실행합니다.

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

기존 DB 볼륨과 원장을 재사용합니다. 기존 폴더/볼륨을 삭제하지 마세요. down -v를 사용하지 마세요.
실제 .env/키 파일을 새 폴더에 복사하지 마세요. API와 웹은 PAPER·실거래 비활성을 유지합니다.

## 2. Healthy 확인 후 미리보기

이미 생성된 DEMO-WINDOWS-ISOLATED-BTC-V1 원장을 사용합니다. --initialize는 다시 넣지 않습니다.

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -c "import getpass,os,runpy,sys; os.environ['LIVE_TRADING_ENABLED']='false'; os.environ['BINANCE_DEMO_API_KEY']=getpass.getpass('DEMO API Key (hidden): '); os.environ['BINANCE_DEMO_API_SECRET']=getpass.getpass('DEMO Secret (hidden): '); sys.argv=['scripts.run_demo','--config','configs/demo-isolated.example.yaml','--account','DEMO-WINDOWS-ISOLATED-BTC-V1','--preview-entry']; runpy.run_module('scripts.run_demo',run_name='__main__')"
```

DEMO API Key/Secret을 본인 PC의 숨김 입력에 차례대로 넣습니다. 화면에 글자가 표시되지 않는 것이 정상입니다.
숨김 입력 불가 경고가 나오면 Ctrl+C로 중단하세요. 키/시크릿/서명/listenKey를 채팅에 보내지 마세요.
오류나 BLOCKED가 나오면 명령을 반복하지 말고 정제 결과만 공유하세요.

## 3. 결과 읽기

- preview=READ_ONLY_NOT_EXECUTABLE: 조회와 기계적 계산이 끝났습니다. 주문 가능 승인이나 전략 신호가 아닙니다.
- LONG/SHORT 양쪽에 현재 호가 기반 지정가·거래소 최소 유효 수량·명목액·2배 기준 예상 초기 증거금·추정 손절 비용을 표시합니다.
- stop_distance_pct=0.5 / target_distance_pct=1은 기능 점검용 임시 계산 가정입니다. 전략/투자 권고나 LIVE 기본값이 아닙니다.
- 손절/익절은 entry와 mark 양쪽을 기준으로 방향을 확인하고 tick/step 규칙을 적용합니다.
- 계좌 availableBalance에 격리 담보를 더하지 않습니다. 미리보기조차 최소 수량이 위험·증거금 한도를 넘으면 거부합니다.
- 시장 symbol/time을 확인하고 5초보다 오래되거나 미래인 호가를 거부합니다. 계산 후 계좌를 다시 조회해 변경되었으면 결과를 내지 않습니다.
- 결과에는 as_of/expires_at가 있습니다. 스크린샷을 보내는 시점에는 이미 만료될 수 있습니다. 표시된 가격/수량을 수동으로 주문하지 마세요.
- orders_enabled=false / live_trading_enabled=false / entry_signal_supplied=false가 유지됩니다.
- algo_order_write_support=UNVERIFIED: 실제 손절·익절 Algo 주문 지원은 아직 확인하지 않았습니다.
- liquidation_buffer=UNVERIFIED_BEFORE_FILL: 아직 포지션이 없으므로 실제 청산가격과 손절 간격을 검증하지 않았습니다.
- 신규 주문 신호 파일/UUID/가짜 확정봉/GateSnapshot을 생성하지 않습니다. 기존 worker나 reserve/submit을 호출하지 않습니다.

키를 제외한 결과 화면을 공유한 뒤 다음 DEMO 수락 시나리오를 검토합니다.
--enable-demo/--watch/--signal/--check-private/--initialize를 미리보기와 조합할 수 없습니다.
모든 거래소 호출은 GET입니다. 내부 DB에는 기존 CLI의 lease/감사/대조 기록이 남습니다.

## 남은 검증

실제 지정가 주문·체결·Algo 손절/익절·부분체결·재시작/UNKNOWN 복구는 여전히 실제 거래소에서 미검증입니다.
자동매매/실거래/Phase 5는 시작하지 않습니다. 이번 미리보기의 실제 Binance 실행도 Windows 후속 확인이 필요합니다.

# DEMO 읽기 전용 주문조건 미리보기 검증 기록

Python 전체: 317 passed / 0 failed / 0 skipped (45.19초).
명령: TEST_DATABASE_URL=<dedicated local PostgreSQL> python -m pytest -q -p no:cacheprovider.
실제 PostgreSQL 격리 schema + localhost WebSocket + 합성 거래소 응답/자격증명을 사용했습니다.
Ruff check/format 및 변경 source 2개 mypy 통과.
기존 301개에 미리보기/CLI 거부 검증 16개를 추가했습니다.
미리보기 성공 경로에서 거래소 호출이 전부 GET이고 원장에 신규 주문이 추가되지 않음을 검사했습니다.
오래된/미래 호가, 시장 종목 불일치, 역전 호가, 쓰기 활성 client, 계좌 변경,
증거금 부족, 최종 대조 후 호가 만료, 실행 플래그 혼용을 거부했습니다.
의존성 lockfile/API·웹 설정/실거래 위험값/MASTER_SPEC/AGENTS는 변경하지 않았습니다.
이 클라우드에서 Binance 인증 또는 주문을 실행하지 않았습니다. Windows 실제 미리보기는 후속 검증 대상입니다.
미리보기 계산은 실제 손절/익절 지원·체결 가능성·수익성을 검증하지 않습니다. Phase 5는 시작하지 않았습니다.
main과 기존 ZIP은 변경하지 않았습니다.
