# 새 백업 사본에서 실제 COMMIT·전체 취소 리허설

RUN_CHECK.cmd 성공 후 RUN_REHEARSE_COMMIT.cmd 한 번만 실행한다.
원본/이전 사본/보조 컨테이너는 수동 시작·삭제하지 않는다. API 키·USB·고객지원은 필요 없다.

이번에는 새로 복원한 사본 DB의 원장·감사 이벤트·DEMO_AUDIT 대기열에 실제 저장한다.
원본에는 기록하지 않는다. 사본은 networknone이며 소비자/API/키 세션을 시작하지 않는다.
기존 사본의 모델 검증을 먼저 반복하고, 새 helper의 socket이 방금 생성한 사본에 연결됐는지
컨테이너 ID·이미지·마운트·네트워크·원본 볼륨 미사용을 확인한 후에만 쓰기를 허용한다.
기본 SQL READ ONLY는 유지하고 해당 사본의 짧은 트랜잭션만 READ WRITE로 전환한다.

원장·이벤트·대기열 각 저장 뒤에 의도적으로 오류를 일으키는3건에서 전체 취소를 확인한다.
이후 정상 COMMIT1건과 같은 작업의 중복 실행 검사를 한다. 날짜/기준금액/정합성/스트림 해제와
COPY_REHEARSAL_ONLY 감사 기록만 바꾸며, 원문 JSONB는 SQL에서 패치해 정밀도를 보존한다.
다른 필드·고점·누적 평가 구성요소·주문34건·미확정4건·이전 감사 기록은 보존한다.
새 사본의 fence만110→111, 일자는11/18·주 시작일11/16로 변경한다.

원본에 남은 owner/lease를 이 도구로 바꾸지 않는다. 사본의 유효 lease도 거부한다.
만료된 owner가 사본에 복원돼 있으면 사본에서만 fence 증가와 함께 owner/lease를 해제한다.
이는 운영 원본의 권한 인수 정책을 승인하거나 구현한 것이 아니다.

사본에 저장하는 완료 기록은 context=COPY_REHEARSAL_ONLY다. 가상 수락 지문/operation과
1970년 시각은 테스트 전용이며 실제 출처 수락 기록이 아니다. 실제 적용 검사에서 거부된다.
원문 기록/주문 ID는 화면·공개 파일로 출력하지 않는다. 사본 이벤트 payload에는 기존과 같이
원장이 들어 있으므로 사본 볼륨도 비공개 보관하고 공유하지 않는다.

COMMIT 후 별도 연결에서 원장·버전·이벤트·대기열을 다시 읽어 확인한다. 결과를 새 private
영수증에 기록하고 helper와 사본 DB를 정상 종료한다. 새 사본 볼륨/영수증은 보존한다.
기존 account/financial/transition_draft 출력은 BEFORE_COPY_TRANSACTION 관측값이다.
새 저장 결과는 copy_commit_review에 있고 original_* 변경/수락 필드는 false여야 한다.
입력 백업 해시와 원본 컨테이너 상태도 끝에 재검사한다.

성공: COPY_COMMIT_REHEARSAL_COMPLETE_COPY_STOPPED_ORIGINALS_UNCHANGED_NO_ORDERS
rollback_cases_verified=3, copy_commit_verified/copy_idempotency_verified=true여야 한다.
실패 시 COMMIT 결과가 불명확할 수 있으므로 재실행하지 말고 화면을 확인한다.
이 성공은 원본 적용·출처 수락·과거 요청 해소·거래 재개 승인이 아니다.
