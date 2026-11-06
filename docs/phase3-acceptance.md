# Phase 3 — 위험관리·지정가 PAPER 검증 보고서

검증일: **2026-10-19 UTC**. 사용자 승인 범위 Phase 3만 구현했다. **Phase 3 오프라인/로컬 수락 조건 충족.**
Phase 4는 시작하지 않았다. [수정 파일](phase3-files.md), [설계/제한](phase3-architecture.md), [실행 지침](runbook.md).

## 구현 결과

- PAPER 전용 불변 정책, 원장/신호 version 검증, 비용 포함 Decimal sizing과 거래소 filter 재검사.
- 계좌/시장/상관 freshness, rules-only/HOLD, 위험/손실/노출/마진/상관 gate.
- PostgreSQL 계좌 잠금 기반 원자 예약, signal/client/fill/operation 중복 제거, lease와 fencing.
- 별도 영속 venue 및 일반/Algo/포지션/exit 상태, intent 선 commit, UNKNOWN→조회 대조, 재시작 복구.
- 부분체결 우선 SL 검증, 잔량 취소 확정 후 예약 해제, 실제 체결 위험 초과/손절 실패 시 긴급 대응.
- PAUSE_ENTRIES / SAFE_STOP / EMERGENCY_FLATTEN 내부 명령과 감사/outbox. HTTP 제어 경로 없음.
- 별도 명시적 CLI 재현/복구와 읽기 전용 PAPER snapshot API. 앱 기동 시 worker 비활성 유지.

## 실제 완료된 검사

최종 코드에서 다음 명령이 exit 0으로 완료됐다.

```bash
TEST_DATABASE_URL=postgresql+psycopg://dev:dev@127.0.0.1:5432/hybrid make check
uv run --frozen alembic check
python -m scripts.compose_cloud up -d --build --wait
python -m scripts.smoke
```

| 검사 | 실제 결과 |
|---|---|
| Python 전체 | **202 passed / 0 failed / 0 skipped** (26.10초) |
| PAPER 통합/장애 검사 | 49개, 실제 PostgreSQL 별도 committed schema 사용 |
| 프런트엔드 | **5 passed**, lint/typecheck/production build 통과 |
| Ruff | check 및 format check 통과, Python 108개 파일 |
| mypy | strict 검사 50개 source 파일 통과 |
| Alembic | 0002_paper upgrade, isolated schema downgrade/base→head, model drift 없음 |
| 앱 기동 | 최종 이미지 build, DB/API/web 모두 healthy |
| 재시작 | api/web restart→up --no-build --wait→HTTP smoke 통과 |
| HTTP | phase=3, DB ready, 주문/LIVE false, 한국어 HTML, web proxy, 시장/PAPER 상태 확인 |
| LIVE guard | 최종 API 이미지를 network=none, mode=LIVE, toggle=true로 시작: **exit 3**, LIVE rejected |
| 보호 문서 | AGENTS.md / MASTER_SPEC_V1_0.md / CODEX_START_HERE.md diff 없음 |

초기 개발 실패도 수정했다: 테스트 전략명을 기존 계약에 맞췄고, 실제 체결 이후 위험 재검사에서
거래별 승인 예산을 수수료 차감 equity로 다시 낮추는 오류를 고쳤다. 거래별 예산은 예약 시 고정하고
포트폴리오 노출/마진은 최신 equity로 검사한다. 최종 검사에 실패/skip은 없다.

## Phase 3 수락 조건과 장애 검증 범위

| 명세 | 검증과 관측 결과 |
|---|---|
| 중복 주문 0 | 같은 signal의 두 thread 예약/submit → DB intent 1개, venue submit_count=1 |
| 동시 예산 초과 0 | 4종목 동시 예약 → 2개 승인, 합산 위험 ≤200 USDT 및 notional ≤15,000 USDT (합성 정책) |
| T-01 | 응답 유실은 UNKNOWN, 조회로 ACK 확인, 무조건 재제출 없음 |
| T-02/03 | 같은 fill 중복은 1회 계산, 지연/역순 notification은 현재 venue 조회로 처리하여 취소/체결 환원 없음 |
| T-04 | step으로 내린 약 30% 체결 보호, terminal 취소 후 잔량 예약 0, 체결 위험 유지 |
| 취소 timeout | 잔량 예약 유지, 추가 체결 보호, 실제 취소 조회 후 해제 |
| T-05 | SL 생성 실패 또는 ACK의 잘못된 방향 → EMERGENCY/진입 차단/reduce-only 시도 |
| T-06 | TP 후 position=0 확인 전 SL 보존, 대조 후 잔여 SL 취소 |
| T-07 | 새 leader는 재대조 전 진입 차단. submit commit 직후 프로세스 유실 상태도 중복 제출 없이 복구 |
| T-08 범위 | 내부 operation_id 중복/충돌/반복 복구 검증. 실제 UI 이중 클릭 E2E는 후속 단계 |
| T-09 | unrealized/realized/fees/funding 손실 잠금, 입출금 조정, KST 경계/고정 주간 시작, 하드락 보존 |
| T-10 | 실제 DB 행 잠금+만료/중복 leader fencing, DB 오류 주입 시 venue 도달 전 차단. PostgreSQL 프로세스 자체 중단은 하지 않음 |
| T-11 범위 | 명시적 HOLD가 새 진입을 거부하면서 기존 SL 보존. 실제 AI 추론/NaN 처리는 Phase 5 범위 |
| T-12/16/18 | 기존 LIVE null/toggle/DEMO 및 Algo endpoint 오프라인 계약 회귀 검사 유지 |
| T-13 | 원자 노출/마진/상관 cap, group 이름 변경으로 회피 불가, 실제 fill notional 증가도 재검사/축소 |
| T-14 | stale/future 시장·계좌·상관 및 원격 revision 변경/보호 검증 중 추가 체결은 진입 차단 |
| T-15 | exit ACK 후 미체결은 UNKNOWN, SL 보존, 중복 exit 없음, 실제 flat 조회 전 완료 아님 |
| T-17 | 미관리/수동 포지션 변화 탐지 → UNHEALTHY/미대조/신규 진입 차단 |

이는 PAPER/기존 오프라인 계약의 범위이며 **실제 Binance T-01~18 전체 완료를 의미하지 않는다.**

## 실제 CLI 산출물

```bash
uv run --frozen python -m scripts.run_paper --demo \
  --policy configs/paper-sandbox.example.yaml --account PAPER-phase3-final \
  --output .cache/phase3-final
```

합성 10,000 USDT 계좌에서 intent/submit 1개, 수량 9.827 중 2.948 체결, 나머지 취소,
SL 검증, TP 후 CLOSED/open_quantity=0/remaining=0을 확인했다. 수수료는 양방향 합계
0.2594240 USDT로 기록됐다. 이는 고정 합성 입력의 회계 검증이며 실제 수익/체결 확률이 아니다.

별도 CLI 프로세스에서 다음과 같이 동일 계좌/정책을 복구했다. recovery JSON은 최초 input의
as_of를 07:00:31 UTC로 바꾸고 RECONCILE 이벤트만 넣었다.

```bash
uv run --frozen python -m scripts.run_paper --resume --replay /tmp/phase3-final-recovery.json \
  --policy configs/paper-sandbox.example.yaml --account PAPER-phase3-final \
  --output .cache/phase3-final-recovered
```

두 실행의 orders=1, filled_orders=1, open_positions=0, reconciled=true 및 동일 체결/수수료 원장을
확인했다. 기존 output/계좌는 일반 실행에서 덮어쓰지 않는다. 결과는 각 폴더의 input.json,
ledger.json, report.json이다. 현재 HTTP 조회에서는 오래된 replay를 stale로 표시하고 worker는 false다.

## 남은 제한과 다음 단계

- 기존 공개 REST/WSS proxy 403은 미해결이다. Phase 3은 오프라인이며 이번에는 공식 문서만 GET 확인했다.
  실제 공개 연결 성공이나 환경 게시/새 작업 복원은 검증하지 않았다.
- PAPER의 원자 close-position SL 갱신, 합성 유동성, 로컬 TP, 가상 clock/30초 lease는 실제 Binance
  지연/queue/청산/주문 교체 경합을 보장하지 않는다. 모든 종목의 상관 위험은 단일 보수 예산이다.
- AI 모델·DEMO 주문·운영 worker·재인증 제어·외부 outbox 전달·실제 브라우저 E2E·호스팅 CI 실행은 미구현/미검증이다.
- 실거래 기능 기본 차단과 기존 OPEN 위험 숫자 12개 null을 유지했다. 샌드박스는 실거래 승인값이 아니다.
- 기존 미커밋 파일/변경을 보존했다. commit/push는 하지 않았다.
- 환경 draft의 **start_skill만 Phase 3 지침으로 저장 확인**. install_script/network/secret은 변경하지 않았다.
  저장은 현재 런타임 적용/게시가 아니다. 환경 설정에서 검토·저장 후 게시해야 하며 새 작업 복원은 이후 검증 대상이다.

다음 작업은 승인 후 **Phase 4 실제 Binance DEMO 체결/보호/복구**다. 공개망 제한부터 해소하고 공식
DEMO 지원 범위를 확인한 후 전용 키 분리, 실제 Algo 조회/체결·취소·재시작·긴급 종료를 검증해야 한다.
이번 작업에서는 Phase 4를 시작하지 않았다.
