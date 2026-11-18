# 전체 JSONB 원장 변경안 및 감사 초안 검증

사용자는 새 격리 사본의 SQL 금융 대조 성공 화면과 다음 단계 지시를 보냈다.
확인된 금융 값·원장 지문·원래 요청 상태를 이전 검증 방식으로 재검사하고, 전체 state
JSONB 복사본 위에서 기간 변경안과 미승인 감사 초안을 계산하는 읽기 전용 도구를 준비한다.

새 사본 복원·마운트·정상 종료 제어는 고정 금융 조회 패키지에서 재사용한다. 원본 API/DB와
기존 사본은 시작하지 않는다. 기존 성공 비교 helper 하나에 대한 엄격한 ID/RO/중지 검사는 유지한다.
income→trade→current 원문/영수증 해시를 다시 검증하고 읽기 공유 스트림을 사본 검증 종료까지
유지한다. 실제 후보 날짜와 금액, 각 영수증 해시 및 manifest를 묶은 증거 지문이 SQL에 전달된다.
이 후보는 2026-11-18/주11-16 저장 관측이며 이후 날 최신 상태라고 하지 않는다.

SQL은 repeatable-read read-only 안에서 CTE/jsonb_set/concat으로 메모리 값을 계산한다.
UPDATE/INSERT/DDL은 없다. 후보 변경은 daily/weekly key/base, reconciled=false,
stream_ready=false 및 operations 감사 초안 항목 한 개다. 나머지 모든 JSONB 필드가 같고
기존 operations가 하나도 바뀌지 않았는지 비교한다. 주문·요청·value·고점·paused는 별도로
보존 확인한다. 알 수 없는 추가 필드도 전체 비교에 포함된다. 실제 테이블 state/fence는 쓰지 않는다.

감사 초안은 UNAPPROVED_PERIOD_REVIEW_DRAFT_V1이며 source_accepted/applied/execution_approved는
모두 false다. before 전체 지문, audit 전 금융 후보 지문, 증거 지문, 후보 필드, expected/proposed
fence를 묶는다. 검토 ID는 before 지문+fence+후보+증거에서 결정해 같은 입력은 같은 ID,
다른 버전/증거는 다른 ID가 된다. audit 항목을 포함한 전체 draft 지문을 출력한다.
순환 hash를 피하려고 audit 안의 후보 지문은 audit 삽입 전 후보에 대한 것으로 구분한다.
메모리 operations 값도 기존 모델의 문자열 값 형식을 유지하고 기존 key는 덮어쓰지 않는다.

두 SQL 출력은 같아야 하며 source 파일 해시는 전후 재검사한다. 감사 초안도 실제 operations에
저장하지 않는다. 실제 승인/채택/감사 event/outbox/CAS/lease 원자적 commit 경로는 구현하지 않는다.
전체 JSONB draft 보존 검증을 실제 commit payload 검증 또는 Pydantic 모델 검증으로 표시하지 않는다.
apply token, source 수락, 7일 제한 예외, 미확정 요청 해소, 최초 자금/자정 평가액 증명 또는
거래 재개를 하지 않는다. 준비 상태를 자동 재사용하지 않도록 후보에서만 정합성/스트림 flag를 해제한다.

실제 PostgreSQL 합성 원장에서 전체 상태가 조회 전후 그대로인 것, read-only 쓰기 거부,
임의 필드/고점/주문/요청 변경 및 잘못된 audit/fence의 차단, 새 증거의 ID 변경을 검사한다.
Windows5.1/NTFS 및 실제 사용자 원장 검증은 사용자 실행을 관측하기 전까지 미검증이다.
