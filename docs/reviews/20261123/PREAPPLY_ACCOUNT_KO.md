# 출처 수락 연결과 새 날짜의 읽기 전용 계정 관측

사용자는 구체적인 출처 수락 질문에 '응 알겠어 다음단계 진행해'라고 답했다. 이를 검토 ID
saved-wallet-source-review-20261118-455f05eb38e26b8a에 한정한 수락으로 기록한다.
기간은11/18 일자·11/16 시작 주, 일/주 기준금액 후보는 각각4998.74183608 USDT다.
정확한 메시지 시각은 제공되지 않아 당시 대화 날짜만 기록했다. SHA256 연결은 대화 기록의
내용 결합이며 외부 서명이나 거래소 증거가 아니다. 이전 검토 JSON은 수락 전의 불변 문서로
그대로 둔다. 현재 수락 기록은 별도 SOURCE_ACCEPTANCE_RECEIPT.json이다.

사용자의11/23 계속 지시에 따라 그 수락을23일/23일 시작 주로 확대하지 않는다. 새 계정 상태를
조회하는 도구를 준비한다. 실제 API 호출은 사용자가 로컬에서 이 도구를 실행할 때만 수행하며
이번 클라우드 작업은 모의 HTTP 응답을 사용한 검사다.

## 수락 기록과 도구 연결

원래 검토안 SHA256과 실제 수락 기록의 canonical SHA256을 코드의 고정 신뢰값에 연결한다.
단순히 입력 파일이 주장하는 해시를 다시 신뢰하지 않는다. 후보/질문/답변/미증명 항목/권한
범위가 바뀌면 기록 지문이 달라져 거부한다. 수락의 scope는 SOURCE_ACCEPTANCE_ONLY다.
원본 시작·쓰기·owner/lease 인수·보호 조건 해제·요청 해소·재전송·거래 승인은 모두 false다.
새 프로그램에 원본 DB 저장 기능이나 적용 토큰 생성 기능은 없다.

수락 기록과 원래 제안 JSON을 sealed 패키지에 넣고 전송 manifest로 보호한다. PowerShell도
키 입력 전에 두 파일의 정확한 bytes SHA256과 검토 ID/제안 지문/권한 범위/기존 저장 후보를
검사한다. SOURCE_ACCEPTANCE.json의 source_accepted=true는 후보 출처만 수락했다는 의미다.
자정 평가·계정 전체 과거 이력·최초 자금 증명이 새로 충족됐다고 표시하지 않는다.

## 실제 조회와 범위

기존4422deb current-reader의 DEMO 전용 GET 전송, rate/timeout/호스트/경로 제한, 비밀 숨김,
계정 식별 anchor, saved income/trade 입력 검사 코드는 바이트 동일하게 재사용한다. 공개 시각과
제한 조회, anchor2개, 설정2차례, 전 종목 일반/조건부 미체결·포지션·계정2차례, 수입/BTC 체결
공백 조회로 정상 경로는18HTTP다. 실제 키는 로컬 숨김 입력으로만 받으며 저장/채팅/환경변수로
옮기지 않는다. API/Docker/원본 DB/키 세션을 시작하거나 설정/주문을 변경하지 않는다.

새 시각은 이전 저장 관측 종료인2026-11-18T05:40:00.911Z보다 뒤여야 한다. 공백 조회 구간은
기존 참조 이후7일 이내로 제한하고 시작과 끝에 검사한다. 조회가 오래 걸려 한계를 넘거나 날짜/
주가 조회 중 바뀌면 중지한다. 이 조회 범위 제한은 기존 원장 적용 함수의7일 제한과 별개이며
어느 제한도 완화하지 않는다. 한계를 넘었다고 자동으로 새 후보나 조회 창을 만들지 않는다.

서버 시각에서 산출한 observed_day/week와 수락된 accepted_day/week를 분리한다. 과거
source의 day_key/week_key 필드는 기존 저장 파일 연결용으로 보존한다. 잔액/포지션/주문/설정/
활동 공백이 모두 맞으면 current_account_profile_matches_saved_reference=true다. 이때도
수락 기간이 현재 관측 기간과 다르면 accepted_period_matches_observed_period=false,
preapply_account_conditions_matched=false, original_apply_ready=false를 유지한다.

정상적으로11/23을 관측하고 참조 프로필도 같으면
CURRENT_ACCOUNT_MATCHED_NEW_PERIOD_REVIEW_REQUIRED_NO_ORDERS를 표시한다.
수락 기간과 같은 기간의 일치는 PREAPPLY_ACCOUNT_MATCHED_NO_ORIGINAL_WRITE_NO_ORDERS다.
두 경우 모두 원본 적용 준비 완료를 의미하지 않는다. 불일치 관측은 private 영수증에 남기고
PREAPPLY_ACCOUNT_NOT_MATCHED_KEEP_STOPPED_NO_RETRY와 종료2를 반환한다. '응답 저장 성공'을
'적용 조건 통과'로 오인하지 않게 한다.

## 저장과 남은 경계

새 private namespace WBKANG-DEMO-PreapplyReads/preapply-account-20261123-UUID에
원문 응답·intent·영수증만 보존한다. 기존 CurrentReads의 유일 참조 선택을 깨뜨리지 않는다.
기존 입력을 잠그고 조회 전후 해시를 확인한다. 부분 실패도 보존하고 자동 재시도하지 않는다.
영수증의 source 수락 지문은 canonical JSON 지문이고, 전송 파일 지문은 bytes SHA256으로
구분한다. 세션에 API 키를 저장하지 않는다.

원본 원장/버전/정지 상태의 현재 확인, 원본 owner/lease 처리 정책, 전용 원자적 원본 적용 경로와
구체적 적용 승인은 아직 남아 있다. 새 날짜의 후보 수락도 별도다. 이전4개 요청은 보존한다.
검증은 실제 PowerShell7의 모의 HTTP·파일·런처 경로이며 Windows5.1/NTFS 및 실제 사용자
계정의 새 응답은 사용자 실행 관측 전까지 미검증이다. 이 작업은 MASTER_SPEC 잠금 조건을 바꾸지 않는다.
