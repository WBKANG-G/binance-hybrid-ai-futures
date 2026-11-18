# 적용용 감사 모델의 사본 메모리 리허설

RUN_CHECK.cmd 성공 후 RUN_REHEARSE_MODEL.cmd 한 번을 실행한다.
고객지원·API 키·USB가 필요 없다. 원본과 기존 컨테이너를 수동 시작/삭제하지 않는다.

고정 백업으로 만든 새 networknone 사본에서 원문 전체를 읽고 기존 초안 검증을 반복한다.
사본 SQL은 READ ONLY다. 추가 helper도 networknone/rootRO/비특권 사용자이며 사본 socket만
RO로 마운트한다. 기존89개 설치 파일과 검증 코드/SQL의 해시를 확인한다.

새 PeriodCompletionAudit 형식으로 완료 기록과 후보 원장을 메모리에만 구성한다.
이 리허설 기록의 context는 COPY_REHEARSAL_ONLY로 고정한다. 내부 테스트 기록의
applied/source_accepted=true는 형식 검사에 쓰는 가상 값이며 사용자 수락이나 DB 적용이 아니다.
시각1970-01-01과 operation/출처 지문도 재현 가능한 테스트용 값으로만 사용한다.
검증용 기록 전체나 적용 payload는 출력/저장하지 않는다. 실제 적용 검사기가 이 기록을
MODEL_REHEARSAL_CANNOT_AUTHORIZE로 거부해야 성공이다. 미승인 초안은 그대로 별도 형식이다.

기존 주문·요청·누적 평가 구성요소·고점·평가 시각·이전 감사 기록을 보존한다.
원장의 실제 operations/events/outbox에는 쓰지 않는다. 새 소스 정책을 운영에 설치하거나
기존 7일 제한을 바꾸지 않는다. 전체 트랜잭션 COMMIT을 백업 사본에 수행하는 단계도 아니다.

새 형식은 키/기간/금액/원문 지문/증거/버전/별도 출처 수락 기록과 연결된다.
형식 검증만으로 출처 수락이나 커밋 사실을 증명할 수 없다. 실제 적용은 별도 검증된 수락
기록과 신선한 계정 관측, 원본 정지·지문/버전 확인, 원자적 저장 도구가 필요하다.
자정 평가액·전체 계정 과거 거래·최초 자금이 독립 증명됐다고 표시하지 않는다.

성공: ADOPTION_MODEL_REHEARSAL_COMPLETE_COPY_STOPPED_NO_ADOPTION_NO_ORDERS
결과의 typed_model_review에 completion_audit_model_validated와
completion_candidate_model_validated, rehearsal_receipt_rejected_for_real_application가 true여야 한다.
실제 source_accepted/database_changed/baseline_adopted는 false여야 한다.
새 사본과 helper를 정상 종료하고 private 결과 파일과 볼륨은 보존한다.
오류가 나면 재실행하지 말고 화면을 확인한다. 원문 영수증/키는 공유하지 않는다.
