# 보존본 잔고·일간/주간 기준금액 읽기

사용자가 초기 지급/충전/잔고 초기화 경위는 모름, 관련 이메일/스크린샷/내보내기 자료는 없음이라고
답했다. 이를 신규 운영자 진술·과거 자료 수락·기준금액 승인으로 변환하지 않는다.
사용자의 다음 단계 요청에 따라 원본/복구 사본을 시작하지 않는 보존본 검사만 준비한다.

Windows에서 실제 성공한 prior-request inspector의 f3f8e44 원본 core를 바이트 동일하게 재사용한다.
고정 manifest pin과 runtime.tar SHA256/크기를 대조하고, 읽기 전용 공유 스트림을 유지한다.
archive 안의 동일 adoption 폴더에 있는 intent/commit-intent/complete만 tar -xOf로 메모리에 읽는다.
신규 디렉터리/파일 생성, 압축 해제, Docker, 키 입력, 네트워크, DB 연결은 없다.

출력은 다음을 구분한다.

- prior_snapshot: 날짜13 채택 이전 old_row.state의 기록. 날짜12인 과거 스냅샷이다.
- recorded_value_components_usdt: initial/realized/unrealized/fees/funding/net_deposits 기록.
- derived_prior_snapshot_equity_usdt: 이 구성으로 Decimal 계산한 수치. 실제 거래소 잔고 증명 아님.
- 기록된 가용잔고/일간·주간 기준액/고점: 초기 입금 근거나 현재 계정 상태와 별개.
- historical_report: 당시 완료 보고서가 주장한 날짜13/주간9의 기준액·고점·근거 유형.
- historical_report_operator_attestation_used: 과거 보고서의 주장이지 이번 사용자 확인이 아님.
- 정확한 자정 평가/계정 전체 이력 내보내기/계정 신원 검증이 없었다는 기록을 유지한다.

정해진 계정/policy, DEMO/paused, 주문34/미해결4, 날짜/주간 연결, 보고된 금액과 이전 스냅샷,
커밋 작업번호/fence 증분 연결 필드를 검사한다. 수치는 28자리 이내 고정 소수점 문자열로만
받고 부동소수점·지수·문화권 쉼표·비유한값을 거부한다. 계산/표시에는 invariant Decimal을 사용한다.
이 검사는 DB의 커밋 해시·감사 체인 전체·현재 원장과의 재대조가 아니다. 관련 검증 값은 false이다.

고점/초기값 5000이 기록되어도 최초 지급·당시 잔고가 입증되는 것은 아니다.
웹의 USDC/BTC를 USDT 잔고/기준에 환산하지 않는다. 초기 지급 근거·잔고 출처 완전성·
현재 DB·신규 운영자 진술·근거 수락·기준금액 채택·실행/주문 승인 모두 false로 유지한다.

사용자 실행: ZIP을 바탕 화면에 압축 해제 → RUN_BASELINE.cmd.
원본/복구 사본 정지 및 USB 분리를 유지한다. 오류 시 고정 오류 코드만 출력하며 원문 상태를
콘솔에 노출하지 않는다. 과거 증빙의 독립 검증과 남은 실제 증빙8개는 자동 완료 처리하지 않는다.
