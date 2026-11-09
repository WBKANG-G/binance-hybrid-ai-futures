# Phase 4 개발 Runbook

작업 디렉터리 `/workspace/binance-hybrid-ai-futures`. 기존 checkout을 사용하고 요청 없이 worktree를 만들지 않는다.
AGENTS.md·명세를 읽고 기존 파일/변경을 보존한다. Phase 4는 사용자 승인 상태지만 실제 DEMO 수락 검증은 외부 차단으로 미완료다. Phase 5 자동 착수는 금지한다.

## 설치·앱 기동

`make install`은 frozen uv/npm 설치다. Python 3.12+ / uv 0.12.23 / Node 24 계열을 사용한다.
호스트 uv가 없으면 기존 환경 설치 스크립트가 `/workspace/.tools/phase0-uv`에 설치하므로 PATH에 추가한다.

```bash
python -m scripts.compose_cloud up -d --build --wait
python -m scripts.smoke
TEST_DATABASE_URL=postgresql+psycopg://dev:dev@127.0.0.1:5432/hybrid make check
uv run --frozen alembic check
```

호스트 별도 실행은 make db→make migrate→별도 터미널 make api/make web다.
ready는 DB SELECT 1 및 revision 0003_demo를 확인한다. cloud helper는 기존 proxy/DNS/공개 CA를
전달하고 TLS 검증을 유지한다. 재시작은 `python -m scripts.compose_cloud restart api web` 뒤
`up -d --no-build --wait`와 smoke로 확인한다. `down`은 개발 DB volume을 보존한다.
프로세스/연결은 snapshot에 보존된다고 가정하지 않는다.

## 연구 실행

```bash
uv run --frozen python -m scripts.run_backtest --demo --folds 2 --test-hours 24 \
  --output .cache/new-research-run
```

새 output 경로를 선택한다. 기존 nonempty directory는 거부한다. 데모는 SYNTHETIC이며 시장 성과가 아니다.
`summary.json`에는 config/data/funding/구현 hash와 구간·가정, `report.html`에는 비교표,
실행별 CSV에는 거래·1m equity·후보가 있다. 재사용 input CSV/JSON도 생성된다.
실제 파일 실행은 README의 --bars/--instrument/--funding 형식을 사용한다.

historical_close는 마감 시각/as_of+latency를 가용성 가정으로 사용한다. 과거 revision/실제 receipt를
복원하지 않는다. captured 모드는 received_at도 기다린다. 입력에 gap·duplicate·역순·forming bar·상장 전
시점이 있으면 중단한다. 펀딩 coverage, minute 경계의 TTL/hold, warm-up/embargo/test 구간을 명시한다.
forward test 시간 안에서만 신호를 사용하고 끝에는 연구 정산한다. 비용이 작아도 수익을 보장하지 않는다.

결과를 비교할 때 source와 hash, 체결 가정, 비용/펀딩, 데이터 가용성, 1m MDD 한계를 함께 확인한다.
재현하려면 같은 입력·profile·코드로 다른 빈 output directory에 실행한다. 보고서/CSV를 임의 덮어쓰지 않는다.

## 공개 시세·안전

기존 collect_market/backfill_market은 공개망 접근이 필요하다. 재검증 시 허용목록의 두 도메인에도 proxy
CONNECT 403이 남아 있다. 플랫폼 proxy 정책/런타임 적용 상태를 확인한 뒤 REST time와 실제 WSS 수신부터 재시도한다.
동일 allowlist를 반복 추가하거나 인증서 검증 해제·proxy 우회로 해결하지 않는다.

실거래 설정의 null 위험 숫자를 연구 profile에서 복사하지 않는다. LIVE/DEMO 시작과 submit은 계속 거부한다.
연구 후보는 PAPER 위험 승인 상태가 아니다. 명시적 PAPER CLI에서도 별도 위험 게이트를 통과해야 한다. AI/브라우저에 위험 변경 권한이 없다.
키/서명/listenKey/전체 환경을 출력하지 않는다. 실제 계정 인증·주문·관리 API는 미노출이다.

## PAPER 실행과 복구

README의 `scripts.run_paper` 명령을 사용한다. API 기동과 독립적이며 HTTP 주문/제어 경로는 없다.
새 PAPER- 계좌/새 output directory가 필요하다. 기본 PAPER config의 null은 그대로 유지한다.
`--demo --policy configs/paper-sandbox.example.yaml`은 합성 가정에만 사용한다.
실행 증거는 `.cache/phase3-final`과 `.cache/phase3-final-recovered`에 있으며 덮어쓰지 않는다.

재시작 복구는 `--resume --replay <recovery.json> --policy <동일 정책> --account <기존 PAPER 계좌>
--output <새 경로>`이다. recovery의 as_of는 가상 clock이며 이전 30초 lease 이후여야 한다.
새 leader는 재대조 이전 새 진입을 할 수 없다. 더 최신 계좌 snapshot이 없으면 기존 보호/종료만
관리하고 새 진입은 stale로 차단된다. 기간 시작 평가가 없으면 UNHEALTHY이며 하드락은 자동 해제하지 않는다.
중복 operation_id는 같은 작업을 이어가며 UNKNOWN exit를 무조건 재제출하지 않는다.
실거래 위험값을 복사하거나 lock을 DB에서 직접 해제하는 방식으로 우회하지 않는다.

`GET /api/v1/paper/status`는 기본 NOT_STARTED/worker_enabled=false이다. `?account_id=PAPER-...`로
영속 snapshot을 읽을 수 있지만 오래된 replay는 stale다. HTTP 주문·resume·flatten POST는 미노출이다.
자세한 장애 범위와 실제 결과는 docs/phase3-acceptance.md를 확인한다. Phase 5는 별도 승인 전 시작하지 않는다.

## DEMO

[Phase 4 재개 지침](phase4-resume.md)의 순서를 따른다. `demo_probe`는 공개 GET/WSS만 사용하며
exit 0 자체가 연결 성공은 아니다. 현재 결과는 REST/WSS blocked, 인증 미실행이다.
`run_demo` 기본은 읽기 전용 preflight이며 DEMO runtime 키가 필요하다. watch/command는 명시적
--enable-demo가 필요하다. 일반 API는 여전히 PAPER 기본·DEMO/LIVE/주문 비활성이다.
ready revision은 0003_demo, API phase는 4다. 기존 PAPER/연구 산출물은 보존한다.
