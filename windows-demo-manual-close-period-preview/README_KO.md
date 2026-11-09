# 수동청산 이후 일자·주간 기준값 읽기 전용 검토

현재 설치된 accountConfig 버전(4e967dc, 생산 조회 코드015fd20)의 소스45개를 확인한 뒤 실행합니다. 이 검사만으로 DB 기준값을 채택하거나 거래를 재개하지 않습니다. --apply 옵션은 없습니다.

## 실행

압축을 바탕화면 windows-demo-manual-close-period-preview 폴더에 풀고 RUN_COMMAND.txt의 두 줄을 CMD에 입력합니다. 2026-11-09 KST에 새로 확인한 ‘수동청산 이후 모든 종목의 추가 거래·입출금/이체·보너스·계정 설정 변경 없음’ 진술을 사용합니다. 실제 실행 날짜가 다르면 CURRENT_DAY_ATTESTATION_REQUIRED로 멈춥니다. 날짜 플래그를 자동으로 오늘 날짜로 바꾸지 마세요.

정상 결과는 OPERATOR_ATTESTED_DAY_WEEK_PREVIEW_NO_DATABASE_CHANGE입니다. old/proposed day/week와 각각의 기준값, high_water_usdt_preserved를 검토합니다. 제안값은 새 서명 잔고를 읽은 후에만 계산하므로 현재 패키지에는 확정된 새 금액을 넣지 않았습니다. 실행 결과를 보관해 다음 검토에 사용하세요.

11월7일 운영자 진술에 따른 일자 기준값 채택의 typed receipt, 원래 검토 값·원장 보존 해시를 대조합니다. 그 기록을 단순히 없애거나 원래 청산/실패 trial을 정상 TP 성공으로 바꾸지 않습니다. 원래 체결2개와 수동 reduce-only 청산 주문을 재조회하고, 원래 평가액 anchor 이후 수입 내역이 두 번 모두 비어 있어야 합니다. 원래 BTC 체결 cohort 외 추가 BTC 체결, 전체 계정 position/open regular/algo 주문, 잔고 변화, margin/leverage 설정 변경은 중단 사유입니다.

현재 USDT 잔고는 기존 채택 당시 평가액과 정확히 같아야 합니다. 기존 일자·주간·drawdown 손실 한도를 먼저 확인하고, high-water를 보존한 채 메모리 복사본에서 새 day/week 기준값의 risk gate를 확인합니다. 실제 원장과 timestamp, 과거 day receipt, 요청·실패·청산 이력, risk state, pause 및 owner/fence/lease는 바꾸지 않습니다.

## 증거 범위

두 자정의 정확한 평가액을 거래소에서 복원한 것이 아닙니다. exact_day_midnight_valuation_verified와 exact_week_midnight_valuation_verified는 false입니다. 모든 종목의 account-wide exchange export를 받은 것도 아니므로 account_wide_trade_export_verified도 false입니다. 현재 운영자 진술과 서명된 BTC·수입·USDT 증거를 결합한 대체 근거의 미리보기입니다. 이전 ‘없음’ 진술을 자동으로11월9일까지 연장하지 않았습니다.

이번 검사는 새 기준값 적용을 승인하지 않으며 baseline_adoption_approved=false, review_token_printed=false를 출력합니다. 일자·주간 값 변경, 위험 상태 초기화, 거래 재개 또는 TP 재시험 명령은 없습니다. 기존 period review/apply의 entry/exit proof 규칙도 변경하지 않았습니다.

## 제한·오류

기존 서명 조회·쿨다운·GET pool을 사용하며 모든 호출은 읽기 전용입니다. 75초 요청 시작 기한, 최대48 HTTP, 시계10회, 분당 공개 한도60%와 추가 최대100 가중치 여유 조건이 있습니다. 마지막 HTTP는 timeout5초만큼 기한 뒤 끝날 수 있습니다. 418/429, 누락 가중치 헤더,1000행으로 잘린 history, 추가 income/체결, 현재 노출이나 원장 변화는 자동 재시도 없이 중단합니다. history 조회는 entry 이후 최대7일 이내만 허용합니다.

DEMO_CREDENTIAL_SESSION_UNAVAILABLE이면 기존 키 세션 상태를 확인하고 필요하면 만료된 임시 세션을 --clear 후 --ensure로 재등록합니다. 키는 숨김 입력에만 넣으세요. 사적 응답 본문이나 키는 결과에 출력하지 않습니다. PRIOR_DAY_* 코드가 나오면 과거 채택 증거와 현재 원장을 검토해야 하며 해시 검사를 우회하면 안 됩니다.

합성 PostgreSQL/HTTPS 계약 검사69개와 컨테이너 소스 검사가 통과했습니다. 검증 범위와 원본 결과는 VALIDATION.json 및 test-results/에 있습니다. 클라우드에서 실제 사용자 거래소 API나 DB에는 접속하지 않았습니다. 실행 결과가 나오기 전에는 현재 signed 잔고·체결 범위가 이번 검사를 통과했다고 주장하지 않습니다.
