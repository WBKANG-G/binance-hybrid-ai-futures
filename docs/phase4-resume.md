> 후속 격리 모드 수정: 현재 Windows 절차는 `ISOLATED_START_HERE_KO.md`를 따릅니다.
> 아래 Cross 전용 설명과 이전 검증 결과는 당시 기록입니다. 격리 모드 실제 거래소 검증은 아직 미완료입니다.

# Phase 4 실제 DEMO 검증 재개

현재는 **외부 수락 검증 미완료**다. Phase 5를 시작하지 않는다. 모든 단계는 전용 가상 DEMO 계좌만 사용한다.

1. 환경 설정에서 저장된 `demo-fapi.binance.com`, `demo-fstream.binance.com` 허용 추가를 검토·저장·게시한다.
   기존 production/public 문서 도메인은 보존한다. 게시가 proxy 403 해결을 보장하지 않으며 플랫폼 정책/런타임
   적용 상태를 확인해야 할 수 있다. 호스트 우회, wildcard 확대, TLS 해제는 하지 않는다.
2. Binance Futures **DEMO에서 발급한 전용 키**를 환경 설정의 안전한 direct runtime 변수
   `BINANCE_DEMO_API_KEY`, `BINANCE_DEMO_API_SECRET`에 입력한다. 값은 채팅/소스/리포트에 넣지 않는다.
   secret은 로컬 HMAC에 필요하므로 proxy placeholder가 아닌 실제 runtime 값이어야 한다.
   generic/LIVE 키 변수를 같은 worker에 주입하지 않는다. LIVE_TRADING_ENABLED=false를 유지한다.
3. 전용 DEMO 계좌의 one-way, single-asset USDT, cross margin과 정책 이하 leverage를 확인한다.
   코드가 이를 자동 변경하지 않는다. 초기 preflight는 다른 포지션/주문이 없는 계좌를 사용한다.
   demo.example.yaml의 숫자는 DEMO 가정이며 LIVE 승인값이 아니다. 실제 fee/trigger 지원을 확인한다.

재사용 개발 환경:

```bash
cd /workspace/binance-hybrid-ai-futures
export PATH="/workspace/.tools/phase0-uv/bin:$PATH"
export UV_CACHE_DIR=/workspace/.cache/uv
export LIVE_TRADING_ENABLED=false
export TRADING_MODE=PAPER
uv sync --frozen
python -m scripts.compose_cloud up -d --build --wait
python -m scripts.smoke
```

실제 공개 연결부터 **새 출력 경로**로 확인한다. probe의 exit 0은 진단 완료이며 REST/WSS의 PASS가 있어야 연결 성공이다.

```bash
uv run --frozen python -m scripts.demo_probe --output .cache/new-demo-probe.json
```

키와 연결이 유효할 때만 다음 읽기 전용 preflight를 실행한다. `--initialize`는 새 내부 원장만 만들고
기존 계좌에 반복하지 않는다. 정상 CLI 종료는 lease를 반환하므로 다음 실행에서 다시 acquire할 수 있다.

```bash
uv run --frozen python -m scripts.run_demo --config configs/demo.example.yaml \
  --account DEMO-dedicated-validation --initialize
```

**다음 명령은 이 환경에서 아직 인증 실행하지 않았다.** prerequisites가 갖춰지면 별도 터미널에서
명시적으로 private session/보호 감시를 시작한다. signal이 없으면 신규 진입을 만들지 않는다.

```bash
uv run --frozen python -m scripts.run_demo --config configs/demo.example.yaml \
  --account DEMO-dedicated-validation --enable-demo --watch
```

한 번의 명시적 후보를 실행하려면 같은 명령에 `--signal <reviewed-demo-signal.json>`을 추가한다.
파일은 `signal`과 `gate` 객체를 포함한다. Signal의 environment=DEMO, risk_profile_version=demo-sandbox-v1,
지정가/stop/target, UUID, 실제 확정봉 및 TTL과 gate의 실제 시장/계좌/상관 freshness, RULES_ONLY 판단이
필요하다. 기존 BACKTEST/PAPER 결과를 자동 변환하거나 계좌 데이터가 없는 가짜 snapshot을 넣지 않는다.
형식은 docs/schemas/Signal.json과 GateSnapshot 모델을 따른다. 실제 시세와 metadata를 제출 직전 재검사한다.

정상 종료 후 pause된 NORMAL 계좌를 재개할 때는 --watch에 `--resume-entries --operation-id <새 UUID>`를
명시한다. 이 작업은 손실/긴급/불명확 보호 잠금을 해제하지 않으며 이후 신호도 모든 gate를 통과해야 한다.
WS 오류 후에는 supervisor를 재실행하여 연결/REST 대조부터 수행한다. 자동 재연결 daemon은 없다.

긴급 명령은 별도 CLI로 수행하며 현재 leader가 실행 중이면 single-writer가 이를 거부한다. 기존 worker를
정상 종료하거나 lease 만료/복구 후 실행한다. 실제 position=0 조회 이전 UNKNOWN을 성공으로 해석하지 않는다.

```bash
uv run --frozen python -m scripts.run_demo --config configs/demo.example.yaml \
  --account DEMO-dedicated-validation --enable-demo \
  --command EMERGENCY_FLATTEN --operation-id <stable-UUID>
```

같은 작업을 재확인할 때 operation-id를 재사용한다. UNKNOWN exit를 중복 제출하지 않는다.
독립 protection worker가 강제 종료/네트워크 단절된 동안 즉시 보호가 수행된다고 가정하지 않는다.
미체결 진입/보호 상태를 새 leader가 먼저 대조해야 한다. DB 직접 수정으로 risk lock을 우회하지 않는다.

필수 외부 관측 기록:

| 수락 항목 | 현재 상태 | 재개 시 기록할 증거 |
|---|---|---|
| DEMO REST/WSS | proxy 403 | REST serverTime, public event, UTC 관측 시각 |
| 인증/키 분리 | 키 없음 | DEMO 계좌/모드 조회, 키/서명 제외한 정제 결과 |
| 정상/부분/미체결 진입 | 미실행 | 일반 client/server ID, 누적 체결, actual positions, 잔량 취소 |
| SL/TP | 미실행 | 별도 Algo ID, 방향/trigger/closePosition/workingType/priceProtect, 실제 상태 조회 |
| TP/SL 종료·잔여 주문 | 미실행 | 실제 exit FILLED + position=0 이후 정리 순서 |
| WS 단절/서버 재시작/UNKNOWN | 로컬 주입만 통과 | 실제 재연결·REST 대조·중복 주문 0 증거 |
| 비상 정지/청산 | 로컬 주입만 통과 | ACK/UNKNOWN과 실제 flat 구분, 중복 operation 증거 |

실제 DEMO partial fill을 만들지 못하면 PASS로 대체하지 않는다. 로컬 에뮬레이션 결과와 지원되지 않는
실제 기능을 분리 기록한다. 특히 SL 미지원은 로컬 성공으로 대체하여 DEMO 신규 진입을 허용하지 않는다.
모든 실제 수락 조건이 충족된 뒤 Phase 4 완료를 보고한다. Phase 5는 이후 별도 승인 대상이다.
