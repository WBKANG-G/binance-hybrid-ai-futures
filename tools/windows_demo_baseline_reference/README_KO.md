# 보존 자료의 잔고·기준금액 읽기

ZIP을 바탕 화면에 압축 해제하고 RUN_BASELINE.cmd를 실행하세요.
USB 연결·Docker 시작·API 키 입력은 필요하지 않습니다.

기존 원본 manifest.json SHA256과 runtime.tar SHA256/크기를 대조하고,
읽기 전용 스트림을 유지한 채 날짜13 채택 폴더의 intent/commit-intent/complete JSON만
메모리로 읽습니다. tar.exe -xOf는 표준 출력으로 읽는 용도이며 디스크 압축 해제는 하지 않습니다.
최대32 폴더와 유일한 고정 해시 보존본을 요구합니다. 완료/커밋은 같은 폴더에서만 선택합니다.

결과의 prior_snapshot은 날짜13 채택 **이전** old_row.state입니다. 일자는 11월12일로
표시될 수 있습니다. historical_report는 당시 저장된 완료 보고서이며 날짜13 채택 후라고
기록된 값을 별도로 표시합니다. 현재 DB나 지금의 계정 상태가 아닙니다.
원장 초기값, 손익·수수료·펀딩·순입금 구성, 이 구성으로 계산한 평가액, 가용잔고,
일간/주간 기준액 및 고점을 각각 구분합니다. Decimal과 고정 소수점 문자열만 사용합니다.
계산 평가액은 저장된 구성의 계산 결과이며 실제 거래소 잔고 검증이 아닙니다.
기록된 initial=5000이나 high_water=5000은 최초 지급 근거를 입증하지 않습니다.
USDC/BTC 웹 자산을 USDT 기준에 합산하거나 환산하지 않습니다.

근거 표시는 당시 보고서의 주장으로 제한합니다. historical_report_operator_attestation_used가
true여도 이번 대화에서 새 운영자 진술·자료 수락을 받았다는 뜻이 아닙니다.
정확한 자정 평가·계정 전체 거래 내보내기·계정 신원 증명의 당시 미확인 상태는 그대로 표시합니다.
new_operator_attestation_received/initial_funding_independently_verified/
baseline_source_accepted_now/baseline_adoption_approved는 모두 false입니다.

커밋의 작업번호·fence 증분·기록된 기준액의 연결 필드를 비교하지만 DB와 커밋 해시를
재대조하거나 과거 감사 체인 전체를 재검증하지 않습니다. 두 결과는 false로 표시합니다.
현재 DB 검증, 초기 지급 근거, 과거 전체 잔고 출처 완전성은 입증하지 않습니다.

원본 영수증/원장 전체·Client Order ID·키·응답 원문은 출력하지 않습니다.
파일 변경·복구·새 날짜/기준액 채택·거래소 요청·주문·서비스 시작·자동 재시도는 없습니다.
실패하면 고정 오류만 출력합니다. 원본/복구 사본은 계속 정지 상태로 유지하고 결과를 확인하세요.
