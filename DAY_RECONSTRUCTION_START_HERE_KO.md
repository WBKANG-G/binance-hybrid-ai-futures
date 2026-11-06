# 사용자 확인을 포함한 USDT 일별 기준 복원 — 미리보기부터 실행

기존 DB가 있는 Galaxybook PC에서 실행합니다. 전체 체결 export 요청은 다시 보내지 않으며, 기존 요청 intent와 export volume을 유지합니다.

이번 경로는 사용자가 '확인된 BTC 종료 체결 이후 현재까지 다른 수동 주문/프로그램/봇 거래가 없다'고 확인한 사실을 수동 근거로 사용합니다. 거래소의 계정 전체 체결 원본을 확보·검증했다는 뜻이 아닙니다. 미리보기와 감사 기록 모두 source=OPERATOR_ATTESTED_FLAT_WITH_SIGNED_USDT_EVIDENCE 및 account_wide_exchange_export_verified=false를 표시합니다. 이 한계를 수용하지 않으면 적용하지 않고 잠금을 유지할 수 있습니다.

1. ZIP을 모두 추출하고 windows-demo-day-reconstruction 폴더에서 infra가 보이는 위치로 이동합니다.
2. Docker Desktop을 켜고 탐색기 주소창에 cmd를 입력한 뒤 실행합니다.

    docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait

3. 모두 Healthy면 읽기 전용 미리보기를 실행합니다. 아래 확인 옵션은 실행 시점까지의 추가 거래 없음 진술을 재확인하는 뜻입니다. 실제로 추가 거래가 있었다면 실행하지 말고 알려주세요.

    docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.review_demo_attested_day --attest-no-other-trades-since-exit

4. DEMO API Key/Secret을 숨김 입력하고 마지막 결과를 대화에 공유합니다. 이 명령은 lease/DB 쓰기/주문/내보내기 생성을 하지 않습니다.

미리보기 조건:
- 기존 로컬 flat 관측의 계정 포지션0 및 모든 진입 주문 종료·미결/포지션 수량0. 취소 주문의 모순된 기록은 이미 감사 정정되어 있어야 합니다.
- 서명된 BTC 진입/종료 체결 대조, 알려진 종료 이후 자정 전/오늘의 추가 BTC 체결0, 종료와 같은 밀리초의 다른 체결0.
- 다른 종목을 포함한 추가 계정 거래 없음은 사용자 확인 근거로 명시하며 거래소 전체 원본 검증으로 승격하지 않습니다.
- 로컬 추적 시작과 과거 flat 관측이 일관되고 누적 입출금 기준/관련 기록이 모두0인 제한된 경우만 지원합니다.
- 해당 flat 관측 이후 계정 income을 반복 조회해 아예 비어 있어야 합니다. 순합계만0인 서로 상쇄되는 거래도 차단합니다. 다른 자산/지원하지 않는 유형/불완전 페이지/경계 시각 모호성도 기존 parser가 차단합니다.
- 현재 single-asset 모드, Isolated BTCUSDT 2x, 포지션/활성 주문0. USDT asset 잔액과 API 총 지갑/마진 잔액 및 과거 flat 평가액이 모두 같고 USDT/총 미실현손익0이어야 합니다. USDC/BTC 보유량을 USDT로 환산하지 않습니다.
- 현재 KST 일자/거래소 스냅샷이 변하지 않고, 기존 주별 기준은 현재 주와 일치해야 합니다. 기존 일·주 손실/낙폭 한도에 걸리면 재설정하지 않습니다. 고점 정리가 필요한 경우도 별도 검토로 차단합니다.

위 조건이 맞을 때만 사용자의 계속된 flat 확인과 서명된 USDT 현금 근거를 결합하여 해당 자정 조정 평가액 후보를 계산합니다. 예전 관측 value.as_of/last_reconciled를 과거 자정으로 고쳐 쓰지 않습니다. 별도 receipt의 boundary_utc는 수동 근거를 포함해 복원한 평가 기준 시각이며 거래소에서 그 시각에 직접 조회한 값이라는 뜻이 아닙니다.

성공 출력: OPERATOR_ATTESTED_DAY_PREVIEW_NO_DATABASE_CHANGE. old_daily_key/base와 proposed_day_kst/proposed_daily_base_usdt, 수동 근거 사용 여부 및 토큰을 확인합니다. 지금까지의 실제 수치가 그대로라면 후보는4999.85274949 USDT이지만 프로그램이 먼저 검증해야 하며 고정 금액을 강제로 넣지 않습니다.

적용은 별도 단계입니다. --attest-no-other-trades-since-exit와 --accept-operator-evidence, --apply-review TOKEN, --operation-id UUID를 모두 지정해야 합니다. 미리보기 결과를 검토한 뒤 구체적인 적용 명령을 받으세요. 이 문서에 사전 승인된 토큰/자동 적용 명령은 없습니다.

실제 적용 시 같은 근거를 재검증하고 토큰이 일치할 때만 daily_key/daily_base와 감사 receipt/operation을 한 트랜잭션으로 저장합니다. 원래 일자·금액, 기준 시각/관측 시각, 알려진 종료 시각, 사용자 확인 범위, 서명 근거 hash, 수동 source와 거래소 전체 export 미검증 표시를 함께 보관합니다. 같은 작업 ID+토큰은 중복 적용하지 않습니다. lease/fence나 처리시간이 어긋나면 정정 트랜잭션이 롤백되며 별도 lease 감사 이벤트는 남을 수 있습니다.

주별 기준/고점/정책/기존 valuation/입출금 기준/요청/주문/과거 감사 및 export 기록은 유지합니다. EMERGENCY/paused도 유지합니다. 일별 기준 복원은 잠금 해제·UNKNOWN 인정·신규 주문 재개와 다릅니다. recovery_eligible=false로 표시합니다. 실거래 및 Phase5로 넘어가지 않습니다. 오류 시 반복 적용·새 작업 ID·DB/volume 초기화를 하지 말고 코드만 공유하세요. 감사 필드가 추가되므로 적용 후 이 버전 또는 이를 포함하는 이후 버전을 유지하세요.
