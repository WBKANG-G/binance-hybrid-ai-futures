# Phase 0 수락 검증 보고서

검증일: 2026-10-19 (Asia/Seoul). **Phase 0 완료. Phase 1 미착수, 사용자 승인 필요.**
현재 머신에서 검증한 결과이며 GitHub 호스팅 CI 실행이나 환경 게시·새 작업 복원을 검증했다는 의미는 아니다.

## 작성·변경 파일과 기능

- README 교체, `.env.example`, ignore 규칙, Python/Node pin, Python/npm lockfile, Makefile 추가.
- `apps/api`: 읽기 전용 health/ready, 시작 시 안전 설정 검증, DB 오류/설정 오류 입력 미노출.
- `apps/web`: React+TypeScript 한국어 개발 상태 화면. API 응답 검증, 연결 오류 표시, KST 시각.
- `packages/domain`: frozen 계약, Decimal·UTC, 제한된 전략/지정가 진입, 일반/Algo ID 분리, 상태 enum.
- `packages/persistence`: 연결 timeout을 둔 `SELECT 1` probe. 애플리케이션 테이블·migration 없음.
- `configs`: PAPER/LIVE 예제와 JSON Schema. 필수 위험 숫자 12개 모두 null.
- `services`, `integrations`, `backtest`: 명세에 맞춘 후속 모듈 자리, 거래 코드 없음.
- `infra`: digest 고정 PostgreSQL/API/web Compose, 비root 앱 이미지, 선택적 cloud build 설정.
- `scripts`: 스키마 생성, cloud Compose 실행기, 실제 HTTP smoke 검사.
- `tests`, `.github/workflows/ci.yml`: 안전성/계약/API/DB/웹 테스트, 린트·타입·빌드 CI.
- `docs`: 결정·위협모델·API·공식 문서 확인·Runbook·공개 계약 스키마·본 보고서.

전체 파일은 [phase0-files.md](phase0-files.md)에 기록했다.
`AGENTS.md`, `MASTER_SPEC_V1_0.md`, `CODEX_START_HERE.md`는 변경하지 않았다.

## 실제 실행 결과

작업 디렉터리: `/workspace/binance-hybrid-ai-futures`.

| 명령/검사 | 실제 결과 |
|---|---|
| `uv lock --check`, `uv sync --frozen`, `npm --prefix apps/web ci --cache /workspace/.cache/npm` | 통과. 고정 lockfile 재설치 성공 |
| `TEST_DATABASE_URL=postgresql+psycopg://dev:dev@127.0.0.1:5432/hybrid make check` | 전체 성공. 실제 실행 시 UV_CACHE_DIR도 workspace 경로로 지정 |
| `uv run --frozen pytest` | **59 passed, 0 failed, 0 skipped**. PostgreSQL 통합 포함 |
| `uv run --frozen ruff check .`, `ruff format --check .` | 통과 |
| `uv run --frozen mypy` | 통과, 13 source files |
| `npm --prefix apps/web run lint`, `run typecheck` | 통과 |
| `npm --prefix apps/web test` | **5 passed**, 1 test file, JSDOM 기반 |
| `npm --prefix apps/web run build` | 통과. TypeScript 검사 및 Vite production bundle 생성 |
| `docker compose -f infra/docker-compose.yml config --quiet` | 통과 |
| `python -m scripts.compose_cloud up -d --build --wait` | API/web 이미지 빌드, DB/API/web 모두 healthy |
| `python -m scripts.smoke` | API health, DB ready, 한국어 HTML, web→API proxy 모두 기대 응답 확인 |
| `python -m scripts.compose_cloud restart api web` 후 `up -d --wait --no-build`, smoke | 재시작 후 모두 재통과 |
| 네트워크 none 컨테이너의 TRADING_MODE=LIVE / LIVE_TRADING_ENABLED=true 시작 | **예상대로 exit 3**. 필수 위험값 미설정 오류 확인, 주문 요청 불가 |
| 실행 이미지에서 UID/CA mount 확인 | 비root API 사용자, `/run/secrets/cloud_ca` 이미지에 남지 않음 |
| 명세·규칙 `git diff --exit-code`, `.env`/key 파일 ignore 확인 | 기준 문서 변경 없음, 위험 파일 ignore, `.env.example`은 ignore 대상 아님 |

호스트: Python 3.12.14, uv 0.12.23, Node 24.19.0, npm 11.9.0, Docker 28.4.0, Compose 2.40.3.
Digest 고정 컨테이너: Python 3.12.15, Node 24.22.0, PostgreSQL 18.6. 모두 선택한 패키지와 실제 실행 호환 확인.

## 안전성 검증

- 테스트를 먼저 추가했고, 초기 미구현 모듈의 collection failure를 확인한 뒤 구현했다.
- 기본 LIVE false, 모든 모드에서 true 토글 거부, LIVE 위험값 개별 누락 12개 검증.
- 위험값을 전부 채운 LIVE도 Phase 0에서는 거부. DEMO도 미구현 상태로 거부.
- 신호/일반 주문의 MARKET 신규 진입, 허용하지 않은 전략, AI 위험 변경/승인 필드 거부.
- 위험 설정 불변·고정 안전 bool, float·비유한·잘못된 범위·naive datetime 거부.
- JSON Schema와 모델 일치, immutable UUID 이벤트와 idempotency 복합키, UTC 정규화 확인.
- 일반 주문·Algo 주문 식별자 혼용 거부. 미보호·UNKNOWN 기본 상태와 flat 수량 계약 확인.
- DB NUMERIC에 18자리 소수 저장/조회가 Decimal과 정확히 일치하는지 실제 DB에서 확인.
- DB 장애는 503. 잘못된 DB URL과 설정 입력이 응답/시작 오류에 노출되지 않음.
- 실제 Binance 주문 endpoint, test-order endpoint 및 WS에 요청하지 않음. 거래 키 요구·저장 없음.

## 발견·해결한 문제

1. 공식 문서 프록시 403: 문서 도메인 요구를 환경 초안에 저장한 뒤 재조회 성공. REST·WS·변경 로그
   HTTP 200과 본문 확인. 공식 근거는 [binance-api-review.md](binance-api-review.md).
2. npm/Docker cache의 읽기 전용 홈 오류: writable workspace cache로 변경.
3. BuildKit DNS와 프록시 신뢰 전달: cloud 실행기로 기존 프록시 DNS/extra_hosts와 공개 CA secret mount 전달.
   TLS 검증·패키지 무결성을 유지했다. 로컬 기본 Compose와 cloud 설정을 분리했다.
4. nonroot API의 소스 읽기 권한: Docker COPY에 소유권을 명시한 뒤 전체 기동·HTTP·재시작 검증 성공.
5. 설정 오류의 원문 노출과 malformed DB URL: 실패 테스트를 추가해 확인하고 정제된 오류/503으로 수정.
6. strict mypy 오류와 TestClient httpx deprecation: 반환 타입을 명확히 하고 지원되는 httpx2 사용. 최종 검사 통과.

## 제한 및 Phase 1 전에 확인할 사항

- 실제 주문, 시세 수집, PAPER 시뮬레이터, AI, DB migration/outbox/fencing, 계좌 대조는 미구현이다.
- 인증/MFA/CSRF/감사 저장소와 제어 API는 후속 작업. 현 API는 로컬 개발 상태 조회 전용이다.
- 상태 enum은 상태 전이·보호 주문 검증 엔진을 의미하지 않는다. T-01~T-18 전체를 통과했다고 주장하지 않는다.
- 웹 단위 테스트와 HTTP/프록시 smoke는 실행했지만 실제 브라우저 E2E는 이번 범위에서 수행하지 않았다.
- GitHub Actions는 구성했고 같은 명령을 로컬 실행했다. GitHub 서버의 CI 결과는 아직 없다.
- [LOCKED] 변경 없음. O-01~O-15와 실거래 위험 수치는 여전히 미확정/승인 대상이다.
- Phase 1 착수 승인 후 공식 API/변경 로그 재확인, REST/WS 어댑터·서명·시장 데이터·migration과
  주문 파라미터/필터·중복·역순·결측·연결 단절 계약 테스트를 진행한다. 실거래는 계속 금지한다.
- 환경 install_script/start_skill 초안 저장과 환경 게시·새 작업 복원은 별개다. 게시/복원 성공을 주장하지 않는다.

## 재사용 환경 설정

환경 설정 초안에 실제 재실행한 `install_script`와 서비스 시작·검증 절차인 `start_skill`을 저장했고
저장 성공을 확인했다. 기존 저장소 설정·위험값 요구사항은 유지했다. 문서 도메인은 developers.binance.com과
www.binance.com을 추가했다. 사용자는 환경 설정에서 변경을 검토·저장한 뒤 게시할 수 있다.
게시와 새 작업 복원은 아직 검증하지 않았다.
