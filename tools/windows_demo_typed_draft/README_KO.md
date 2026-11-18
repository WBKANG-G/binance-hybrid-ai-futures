# 원장 모델·미승인 감사 형식 검증

RUN_CHECK.cmd 성공 후 RUN_REVIEW_MODEL.cmd 한 번을 실행한다. 원본과 기존 사본/보조 컨테이너는
수동 시작·삭제하지 않는다. API 키·USB·고객지원은 필요 없다.

새 networknone 사본을 복원해 전체 JSONB 초안을 다시 계산한다. 추가 검증 helper는 사본 socket
하나만 RO로 마운트한다. 네트워크 없음·rootRO·비특권 사용자·cap모두제거·재시작no다.
고정 설치 코드89개 해시와 검증 모듈/SQL 해시를 확인하고 원장 전체를 메모리에서만 읽는다.
기존 원장과 후보를 DemoLedger 모델, 감사 초안을 extra금지/엄격한 별도 모델로 검사한다.
초안이 기존 AttestedPeriodBaseline 채택 완료 형식으로 받아들여지면 실패시킨다.

SQL 두 번과 Python 검증 두 번의 원장/초안 지문·검토 ID·증거·fence가 모두 같아야 한다.
기존 모델이 입력을 기본값으로 보완하더라도 raw JSON 비교에서 필드 보존 여부를 별도로 확인한다.
전체 원장·원래 주문 ID·모델 오류 원문은 출력하지 않는다. 알려진 성공/실패 코드만 표시한다.

SQL 초안의 typed_ledger_model_validated=false는 SQL 단계만의 표시다. 별도 typed_model_review의
before/candidate/audit_model_validated=true가 이번 모델 검사 결과다. 두 단계를 혼동하지 않는다.
이 검증은 미승인 초안의 형식과 보존 여부만 확인한다. 실제 채택 완료 감사 기록·DB commit
payload·CAS/lease/event/outbox 트랜잭션·적용 토큰·기간 source 수락·거래 재개를 만들지 않는다.
기준금액·원장·요청 상태는 그대로다. 실제 source 수락 후에는 완료 기록을 별도로 만들고 재검증해야 한다.

성공: TYPED_PERIOD_DRAFT_REVIEW_COMPLETE_COPY_STOPPED_NO_ADOPTION_NO_ORDERS
완료 후 새 사본 DB와 검증 helper는 중지하고 private 보고서/새 볼륨은 보존한다.
실패하면 자동/수동 재시도하지 말고 화면을 확인한다. 실제 파일·영수증 원문·키는 공유하지 않는다.
