# 사용자 확인을 포함한 USDT 일별 기준 복원 검증

전체570개 테스트 통과(89.34초), mypy78개 소스 통과, 전체 Ruff lint/format 검사 통과. 실제 PostgreSQL 및 합성 거래소 응답·로컬WS 사용. 실제 사용자 DB의 기준값 변경/거래소 인증/주문/추가 export 요청은 클라우드에서 수행하지 않았습니다.

근거 방식: 사용자가 확인된 BTC 종료 이후 현재까지 수동 주문이나 다른 프로그램/봇 거래가 없었다고 답했습니다. 이 진술은 사용자 확인 근거입니다. 계정 전체 거래소 원본 검증과 구분하며, 모든 미리보기·적용 출력·감사 receipt에 source=OPERATOR_ATTESTED_FLAT_WITH_SIGNED_USDT_EVIDENCE와 account_wide_exchange_export_verified=false를 명시합니다. 사용자 진술만으로 자동 채택하지 않습니다. 기본 미리보기에 명시적 진술 옵션이 필요하고, 실제 채택은 별도로 --accept-operator-evidence와 토큰/고정 작업 ID까지 요구합니다.

계산 경로는 제한적입니다. 기존 평탄 관측과 valuation 시각이 일치하고 순입출금 기준/관련 기록0, 현재 주간 기준 일치, 서명된 알려진 BTC 완전 종료 및 그 이후 BTC 체결0, 관측 이후 income의 반복 조회가 모두 빈 경우만 지원합니다. 현재 single-asset/Isolated BTCUSDT 2x/포지션0/활성 주문0을 확인합니다. 현재 USDT 자산/총지갑/마진 잔액이 과거 관측 평가액과 같고 총/USDT 미실현손익0이어야 합니다. 사용자 flat 확인과 이 서명된 현금 근거를 결합해 그 자정의 조정 USDT 평가액 후보를 도출합니다. 다른 자산을 USDT로 더하지 않습니다. 과거 모든 계정 포지션을 거래소 원본으로 검증했다고 승격하지 않습니다.

기존 일/주 손실·낙폭 한도에 이미 해당하거나 고점 갱신/주간 기준/입출금 정리가 필요하면 차단합니다. 기준을 채택한 probe로 현재 account_risk 게이트도 다시 평가합니다. 원래 account_risk 함수의 정확한 경계 guard 및 위험 규칙은 변경하지 않았습니다. 이 별도 수동 근거 채택 경로는 명시적 수용 후에만 원장의 daily_key/daily_base를 변경합니다. 기존 value.as_of/last_reconciled를 과거 자정으로 조작하지 않습니다. receipt의 boundary는 복원된 평가 기준 시각이며 그 시각 직접 조회를 주장하는 것이 아닙니다.

새31개 테스트는 수동 source/전체원본 미검증 표시, 미리보기 DB·이벤트 불변과 GET-only, 단일 자정 일별 기준+감사 이외의 원장/요청/주문/주별 기준/고점/입출금/관측 시각/EMERGENCY/paused 보존, 원래 일자·금액과 근거 receipt 저장, 중복 적용 방지, 진술·수용 옵션 없는 실행 차단, 잘못된 입출금 기준/추적시각/주간 기준/기존 손실·낙폭/고점 차단, 추가 체결·서로 상쇄돼 순합계0인 income·잔액 변화·USDT 자산 누락/다른 자산 합산/미실현손익 불일치 차단을 검증합니다. 계정/일자/토큰 불일치, old leader fencing, 검토 중 lease 만료 시 기준/receipt/정정 이벤트 롤백, CLI 미리보기 무쓰기/키 정리/수동 source 출력, 명시적 수용 후 별도 적용/잠금 보존도 확인합니다.

토큰은 원본 ledger(lease 획득 시 무효화하는 reconciled/stream_ready 제외), 계정ID, 수동 source, 해당 자정, 알려진 종료 시각, signed 체결/income hash·스냅샷·후보를 묶습니다. 적용 시 GET 근거를 재검토해 토큰이 일치할 때만 lease/fence와 행 잠금 아래 daily_key/daily_base, 별도 receipt/operation을 원자 저장합니다. 상태 점검용 reconciled/stream_ready는 false로 유지합니다. 위험 정책·주별 기준·고점·기존 valuation·순입출금·과거 요청·주문·감사·export intent는 유지합니다. 별도 lease 획득/해제 감사 이벤트는 남을 수 있습니다. 적용 후에도 EMERGENCY/paused 및 recovery_eligible=false입니다.

변경 범위: 새 서비스/CLI/통합 테스트/안내·검증 문서 및 DemoLedger의 기본값 있는 receipt 필드. MASTER_SPEC/AGENTS, 위험·주문·recovery·기존 정정·저장 구현, 의존성 lock, DemoClient, Isolated 설정, Dockerfile/Compose 및 export 코드는 이전ZIP과 바이트 동일합니다. 기존 PostgreSQL 및 export named volume을 그대로 보존합니다. export 생성은 재시도하지 않습니다. API/web PAPER, LIVE=false 유지.

제한: 이 방식은 사용자 확인에 의존합니다. 사용자가 알지 못한 다른 계정 거래/접근을 거래소 전체 원본으로 배제하지 못합니다. 자동 원본 검증 경로라고 주장하지 않습니다. 사용자가 이 한계를 수용하지 않으면 기준을 채택하지 않고 잠금을 유지해야 합니다. 실제 계정의 최신 조건은 Windows 미리보기에서 확인해야 합니다. 다른 입출금·현재주 변경·추가거래·지갑 변화 등이 있으면 이 제한된 경로를 적용하지 않습니다. 실제 사용자 DB 채택, main 변경, 잠금 해제, 실거래, Phase5 진입 없음.
