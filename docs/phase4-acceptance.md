> 후속 격리 모드 수정: 현재 Windows 절차는 `ISOLATED_START_HERE_KO.md`를 따릅니다.
> 아래 Cross 전용 설명과 이전 검증 결과는 당시 기록입니다. 격리 모드 실제 거래소 검증은 아직 미완료입니다.

# Phase 4 — DEMO 구현·검증 진행 보고서

검증일: **2026-10-19 UTC**. 사용자 Phase 4 승인 범위에서 구현했다.
**코드와 로컬 수락 검사는 통과했지만, 실제 Binance DEMO 수락 조건은 미충족이다.**
이유는 실제 공개 REST/WSS proxy 403과 DEMO runtime 키 부재다. Phase 4 전체 완료를 선언하지 않는다.
Phase 5는 시작하지 않았다. [변경 파일](phase4-files.md) · [설계/제한](phase4-architecture.md) · [재개 지침](phase4-resume.md).

## 구현한 기능

- DEMO-only 고정 REST/WSS, production/redirect/혼합 키 차단, HMAC·clock/recvWindow·cooldown·정제 오류.
- mode를 검증하는 공유 위험 수학, 실제 DEMO metadata·계좌·레버리지/마진/잔량 예약 재검사.
- 별도 DEMO DB 원장과 감사/outbox, lease/fencing/계좌 행 잠금, 의도 선 commit과 effect 직렬화.
- LIMIT 진입, 중복 신호/누적 fill 처리, UNKNOWN 원본 ID 조회, terminal 취소 후 예약 해제.
- 우선 SL 생성·조회 검증, 이후 TP 생성·조회 검증, 일반/Algo/exit client ID와 server ID 분리.
- API 장애에서도 최소 positionRisk 조회로 reduce-only 시도, ACK와 flat 구분, 잔여 SL 보존.
- partial entry의 TP 종료 후 잔량 먼저 취소, confirmed fill 후 잔여 노출만 새 exit 의도로 축소.
- private session 생성/갱신/종료, handshake/pong/끊김·만료 시 fail-closed, 정상 종료 잔량 취소 시도.
- 내부 긴급 명령의 stable operation UUID, NORMAL 계좌만 명시적 resume, 하드락 유지.
- 읽기 전용 preflight와 명시적 CLI worker, 기본 비활성 API/웹과 DEMO snapshot 조회.

## 최종 로컬 검사 결과

```bash
TEST_DATABASE_URL=postgresql+psycopg://dev:dev@127.0.0.1:5432/hybrid make check
uv run --frozen alembic check
python -m scripts.compose_cloud up -d --build --wait
python -m scripts.smoke
```

| 검사 | 실제 결과 |
|---|---|
| Python 전체 | **264 passed / 0 failed / 0 skipped**, 61.93초 |
| DEMO 통합 | 30개, 실제 PostgreSQL의 committed isolated schema + 합성 Binance 응답 |
| DEMO transport/CLI | 28개, 고정 host/서명/거부/timeout/clock/키 격리 및 실제 CLI subprocess |
| DEMO private stream | 2개, 실제 localhost WS socket + 합성 session/REST |
| 프런트엔드 | **6 passed**, lint/typecheck/production build 통과 |
| Ruff | check/format 통과, Python 123개 파일 |
| mypy | strict, 60개 source 파일 통과 |
| DB | 0003_demo upgrade, downgrade/base→head 및 metadata drift 검사 통과 |
| Docker | 최종 API/web 이미지 build, DB/API/web healthy |
| 실행/재시작 | HTTP smoke, api/web restart→up --no-build --wait→smoke 통과 |
| LIVE guard | 최종 API 이미지 network=none, LIVE/toggle=true → **exit 3 / LIVE rejected** |
| DEMO API guard | 일반 API를 DEMO로 시작 → **exit 3**, 독립 CLI 사용 요구 |
| DEMO CLI guard | 최종 이미지 network=none, 키 없음 → **exit 2**, 인증·원장 초기화 이전 거부, traceback 없음 |
| 보호 조건 | 기존 12개 LIVE/PAPER application 위험값 null, AGENTS/MASTER_SPEC/CODEX_START_HERE diff 없음 |
| 실제 계좌 실행 여부 | 기본 DB의 demo_accounts=0, 인증/주문 실행 없음 |

기존 202개 검사에 DEMO/상태/schema 62개를 추가했다. 로컬 통과는 실제 거래소 동작·체결 확률·수익성을 뜻하지 않는다.
개발 중 이름/CLI argument 연결 오류와 보호 조회 실패 경로를 수정했고 최종 검사에는 실패/skip이 없다.

## 장애 수락 검사와 실제 DEMO의 구분

| 항목 | 로컬에서 관측한 결과 | 실제 DEMO |
|---|---|---|
| 응답 유실 | 원본 ID 조회, POST 1회, -2013에도 예약 유지 | 미실행 |
| 중복/역순 이벤트 | REST 누적 fill 단조 검사, 중복 수량 증가 없음 | 미실행 |
| 부분체결·취소 timeout | SL 우선 검증, late fill 보호, terminal 잔량 해제 | 미실행 |
| SL 실패/잘못된 방향·priceProtect | 진입 잠금/긴급 reduce-only 시도 | 미실행 |
| Algo 조회 장애 | 최소 positionRisk로 reduce 시도, 기존 SL 임의 삭제 없음 | 미실행 |
| TP 종료 | 실제 exit FILLED/position=0 뒤 SL 정리, partial 진입 잔량 먼저 취소 | 미실행 |
| exit ACK/중복 명령 | ACK는 UNKNOWN, 추가 POST 없음, 확인된 flat만 성공 | 미실행 |
| 재시작/리더 충돌/DB 오류 | lease/fence·조회 복구, side effect 이전 거부 | 미실행 |
| private WS 손실 | fresh 연결/REST 대조 전 진입 금지, 정상 종료 pause/SL 보존 | localhost만 검증 |
| 수동 포지션·레버리지 변경 | mismatch/정책 cap 거부, 자동 계좌 설정 변경 없음 | 미실행 |
| 계좌/입출금 | equity·중복 transfer·손실 잠금 반영 | 실제 income/asset 계약 추가 검증 필요 |
| 재개 | 명시적 NORMAL/fresh/대조 이후만 허용, 하드락 해제 없음 | 미실행 |

정상/부분/미체결 주문과 실제 Algo 지원·계좌 대조·WS 단절·서버 재시작·긴급 정지 수락 조건을
합성 응답으로 통과했다고 바꾸지 않는다. 실제 미지원 기능은 확인 후 별도 제한으로 기록해야 한다.
특히 실제 SL 미지원은 PAPER 결과로 대체해 신규 DEMO 진입을 허용할 수 없다.

## 실제 외부 확인

공식 General Info/Trade/Account/User Data Streams/change log 문서를 HTTPS GET으로 확인했다.
General Info의 DEMO host와 현재 private route/Algo 계약을 사용했다. 상세 출처는 설계 문서에 있다.

- `demo-fapi.binance.com/fapi/v1/time`: 실제 public GET이 **Tunnel connection failed: 403 Forbidden**.
- 기존 `fapi.binance.com/fapi/v1/time`도 같은 CONNECT 403.
- 실제 DEMO public WSS: **InvalidProxyStatus / HTTP 403**.
- runtime `BINANCE_DEMO_API_KEY`/`BINANCE_DEMO_API_SECRET`: 이름/존재 여부만 검사했고 둘 다 없음.
- 실제 CLI default preflight는 키가 없으면 인증 전에 exit 2로 거부했다.
- 어떤 실제 주문/test-order/인증 계좌 요청/listenKey 생성도 수행하지 않았다.

기록: `.cache/phase4-public-probe.json`, `.cache/phase4-runtime-checks.json`.
probe는 credentials_used=false/orders_sent=0이며 REST=NETWORK_OR_PROXY_BLOCKED,
websocket=PROXY_OR_HTTP_403이다. probe exit 0은 진단 완료일 뿐 연결 성공이 아니다.

## 환경 설정 저장과 다음 작업

기존 허용 목록을 유지하고 **demo-fapi.binance.com, demo-fstream.binance.com** 두 도메인을 추가했다.
두 DEMO direct runtime 변수 요구사항 및 Phase 4 `start_skill`을 draft에 저장 확인했다.
install_script, 기존 도메인/preset, 기존 바인딩, dependency/lockfile/DB volume은 보존했다.
저장은 실행·런타임 적용·게시를 의미하지 않는다. 환경 설정에서 검토·저장·게시가 필요하다.
키 값은 채팅이 아닌 안전한 runtime 환경 설정에 입력해야 한다. HMAC secret은 proxy placeholder로 대체할 수 없다.

[재개 지침](phase4-resume.md)에 따라 공개 연결→인증 read-only preflight→실제 DEMO 수락 시나리오를
진행해야 한다. 플랫폼 proxy 정책/런타임 적용 상태 확인이 필요할 수 있으며 게시만으로 해결을 보장하지 않는다.
반복 승인이나 새로운 Phase 승인은 필요하지 않다. **이미 승인된 Phase 4 실제 검증을 이어갈 상태**다.

추가 제한: 자동 private reconnect daemon/외부 process supervisor/강제 종료 중 즉시 보호 SLA,
외부 outbox dispatcher, 인증 웹 제어, 실제 browser E2E/hosted CI, 게시/새 작업 복원은 검증하지 않았다.
계좌 equity의 세부 PnL 분해와 실제 income/fee asset 지원도 외부 검증 대상이다.

기존 미커밋 파일을 보존했고 commit/push를 하지 않았다. 다음 Phase로 진행하지 않았다.

## 후속 요청 재검증 — 2026-10-19 14:31 UTC

사용자의 다음 스텝 요청에 따라 미완료된 Phase 4 실제 검증의 선행 조건을 다시 확인했다.

- 현재 runtime의 BINANCE_DEMO_API_KEY/BINANCE_DEMO_API_SECRET은 둘 다 없음(값이 아닌 존재 여부만 확인).
- 환경 draft revision 10에는 두 DEMO 도메인이 저장되어 있고 키 변수 두 개는 pending requirement다.
  저장된 설정만으로 현재 런타임 반영/게시 여부를 입증할 수 없다. 이번 확인에서는 설정을 중복 저장하지 않았다.
- 새 실제 public probe: REST=NETWORK_OR_PROXY_BLOCKED, WSS=PROXY_OR_HTTP_403.
  기록: `.cache/phase4-next-step-probe-20261019.json`, credentials_used=false, orders_sent=0.
- 기존 서비스의 실제 HTTP smoke 통과. 인증/주문 요청은 실행하지 않았다.
- 코드 변경/전체 테스트 재실행은 하지 않았다. 앞서 기록한 264/6 검증 결과는 이전 최종 코드 검사 기록이다.

다음 실행에 필요한 외부 조치는 동일하다: 환경 설정에서 DEMO 전용 runtime 키 두 개를 안전하게 입력하고,
저장된 DEMO 도메인 허용/시작 지침을 검토·저장·게시하여 현재 실행 환경에 반영한다. 그 이후에도 403이면
플랫폼의 proxy 정책/런타임 적용 상태 확인이 필요하다. 실제 연결·키가 준비되면 같은 Phase 4를 이어간다.
Phase 5로 진행하지 않았다.

## 후속 실행 재검증 — 2026-10-20 02:03 UTC

- 직접 runtime DEMO key/secret 두 변수 모두 없음. generic/LIVE 키 변수도 없음. LIVE toggle은 false.
- draft revision 10의 두 DEMO 변수는 여전히 pending requirement이며, DEMO 도메인 허용 항목은 저장되어 있다.
- 새 실제 public probe 결과 REST=NETWORK_OR_PROXY_BLOCKED, WSS=PROXY_OR_HTTP_403.
  기록: `.cache/phase4-probe-20261020.json` (credentials_used=false, orders_sent=0).
- API/DB/web HTTP smoke 통과. 실제 인증/주문은 실행하지 않았다. 전체 테스트는 재실행하지 않았다.
- 현재 필요한 외부 작업은 안전한 환경 설정에서 두 DEMO runtime 값을 제공하고 저장/게시/런타임 반영을
  확인하는 것이다. 설정이 반영된 후에도 403이면 플랫폼 proxy 정책 확인이 필요하다.
- 기존 Phase 4 승인은 유지되며, 실제 수락 검증은 차단 상태다. Phase 5는 시작하지 않았다.
