# 전체 JSON 원장 변경안과 감사 초안 검사

RUN_CHECK.cmd 성공 후 RUN_REVIEW_DRAFT.cmd를 한 번 실행한다. 원본·기존 사본은 수동 시작/삭제하지 않는다.
API 키·USB·고객지원이 필요 없다. 기존 income/trade/current 파일을 연결해 11월18일/11월16일,
4998.74183608 USDT의 저장 후보와 증거 지문을 얻는다. 이후 날의 최신 금액으로 표시하지 않는다.

고정 백업에서 새 networknone 사본을 복원하고 SQL repeatable-read/read-only에서만 변경안을
계산한다. 실제 UPDATE/INSERT는 없다. 변경 후보는 daily/weekly key/base, reconciled=false,
stream_ready=false와 operations의 감사 초안 항목 한 개다. 기존 값·고점·주문·미확정 요청·
정책·기타 모든 필드 및 이전 operations가 그대로인지 전체 JSONB를 대조한다.

감사 초안은 UNAPPROVED_PERIOD_REVIEW_DRAFT_V1, source_accepted=false, applied=false다.
전후 지문·후보·증거 지문·변경 전/후 fence가 연결되고, 미리 존재하는 review ID는 덮어쓰지 않고 차단한다.
동일 원장/후보/증거는 같은 검토 ID와 지문을 만든다. SQL 결과는 두 번 같아야 한다.
원래 테이블 state는 변경하지 않고 초안도 실제 operations에 저장하지 않는다.

성공은 JSON 전체의 초안 변환과 보존 검증이다. Pydantic 원장 모델 검증, 실제 commit payload,
기간 source 수락, 자정 평가액/최초 자금 증명, 미확정 요청 해소, 7일 초과 적용 경로,
기준금액 채택 또는 거래 재개 승인이 아니다. apply token도 만들지 않는다.

원본은 시작하지 않으며, 새 사본만 조회 후 정상 종료하고 private 결과와 새 자원을 보존한다.
실패하면 재시도하지 말고 화면을 확인한다. 원본파일·영수증 원문·키는 공유하지 않는다.
성공: PERIOD_DRAFT_REVIEW_COMPLETE_COPY_STOPPED_ORIGINALS_UNCHANGED_NO_APPLY

허용된 필드라도 선언한 후보 값과 실제 메모리 원장 값이 다르면 차단한다. 변경 필드 이름뿐 아니라 값·형식도 증거에 묶인 후보와 일치해야 한다.
