# 취소·미체결 주문의 로컬 포지션 기록 정정 검증

전체 테스트 509개 통과(79.81초). mypy 74개 소스 통과. 전체 Ruff lint/190개 파일 format 검사 통과. 실제 PostgreSQL과 합성 거래소 응답·로컬 WS를 사용했으며 실제 사용자의 Binance 인증/주문 또는 DB 정정은 수행하지 않았습니다.

새 테스트 25개는 기본 미리보기의 DB·이벤트 불변과 GET-only, 제안 필드의 명시, 정정 시 정확히 세 주문 필드만 변경됨, 나머지 주문/과거 요청/정책/평가액/일·주 기준/고점/입출금/관측 시각/EMERGENCY/paused 보존, 정정 전 주문 전체를 포함한 감사 receipt, 같은 작업 ID·토큰의 중복 정정 방지, 상충 토큰/기존 작업 ID 충돌 차단을 검증합니다.

또한 원본 기록·지갑·체결 이력·날짜·대상 계정·토큰이 바뀌거나 원래 취소 주문에서 체결량이 발견되거나 주문이 조회되지 않거나 실제 포지션이 생기면 차단합니다. 알려진 종료 체결을 찾지 못하거나 그 이후·종료와 같은 밀리초·오늘의 추가 BTC 체결이 있으면 정정 대상으로 승인하지 않습니다. old leader fencing과 재검토 중 lease 만료 시 정정·receipt·정정 감사 이벤트 롤백을 확인했습니다. CLI의 명시적 적용 분리, 기본 실행 시 lease/쓰기 없음, 잘못된 적용 옵션의 키 입력 전 차단, 키 미출력/환경 정리도 확인했습니다.

기본 명령은 읽기 전용 미리보기입니다. 적용 시에는 미리보기의 64자리 토큰과 UUID 작업 ID가 모두 필요합니다. 토큰은 대상 계정, 원본 ledger(lease가 무효화하는 reconciled/stream_ready 제외), 거래소 스냅샷, 체결/income 근거, KST 일/주를 묶습니다. 적용은 lease/fence와 행 잠금 아래에서 같은 GET 검증을 재수행하고, 토큰이 일치할 때만 한 트랜잭션으로 수량/위험 금액/포지션 상태를 정정하며 원본 주문과 근거를 영수증에 보관합니다. 적용용 lease 획득/해제는 별도 감사 이벤트를 남길 수 있습니다.

정정 필드는 해당 취소 주문의 open_quantity=0, actual_risk=0, position_state=CLOSED 세 개입니다. entry_state=CANCELED, filled=0, remaining=0, exit_state=UNKNOWN, exit_id와 모든 기존 요청 상태는 보존합니다. 다른 실제 체결 주문은 바꾸지 않습니다. ledger의 reconciled/stream_ready는 false로 유지하고 EMERGENCY/paused는 해제하지 않습니다. 새 감사 필드는 기본값이 있어 이전 ledger를 읽을 수 있으며, 원래 이벤트를 수정·삭제하지 않습니다.

변경 범위: 새 정정 서비스/CLI/통합 테스트/안내서/검증 문서, DemoLedger의 감사 receipt 필드, 기존 읽기 전용 cancelled-history 결과에 검증 스냅샷 fingerprint 추가. 원래 주문 실행 코드, history guard, recovery 적용 코드, PostgreSQL 저장/lease 구현, 위험 계산, MASTER_SPEC/AGENTS/의존성 lock/Compose/Isolated 설정은 이전 ZIP과 바이트 동일합니다. Compose는 PAPER/LIVE=false와 기존 hybrid-windows_postgres_data 볼륨을 유지합니다.

제한: 실제 DB 정정은 아직 실행하지 않았습니다. 사용자의 최신 미리보기 토큰으로 별도 적용해야 합니다. 정정 후에도 recovery_eligible=false이며 과거 UNKNOWN/SUBMITTING 요청과 자정 평가액/손실 기준 문제는 남습니다. BTC 이력만으로 모든 종목의 과거 포지션이나 자정 계정 평가액을 증명하지 않습니다. main 변경, 실거래, Phase 5 시작 없음.
