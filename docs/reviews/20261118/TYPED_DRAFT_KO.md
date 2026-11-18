# 원장 모델 및 미승인 감사 초안 형식 검증 준비

사용자는 전체 JSONB 초안 검증 성공 화면과 다음 단계 준비 지시를 보냈다. 화면에서 실제
변경 후보는 daily_key/weekly_key/operations였고, expected/proposed fence는110/111이었다.
원장·고점·요청·기존 operations가 보존되었지만 typed 모델과 commit payload 검증은 false였다.
이번 준비는 source 수락이나 완료 기록 작성 승인이 아니다.

새 고정 백업 사본의 기존 read-only JSONB 절차를 유지한다. 별도 helper는 socket 하나만 RO,
networknone/rootRO/capALL제거/비특권UID10001/restartno로 만들고 고정89개 설치 파일 해시,
모델 검사 모듈과 SQL 해시를 검증한다. 앱의 DemoLedger 모델을 실제 원장과 후보에 사용한다.
원문 전체는 helper 메모리에만 유지하고 PG text 해시를 재계산해 SQL/호스트 초안 지문과 대조한다.
Psycopg bind는 텍스트 형식을 명시해 psql 문자열 리터럴과 같은 JSON 값/지문을 계산한다.

감사 초안은 extra금지/strict 별도 모델로 검증한다. 날짜·주 경계·양수금액·두 기준금액 일치,
정합성/스트림 준비 해제, fence증가1, source/applied/execution false를 요구한다.
이 초안이 기존 AttestedPeriodBaseline 완료 형식으로 수용되면 차단한다. 완료 기록을 위조하거나
사용자 진술을 새 수락으로 확장하지 않는다. 실제 원장 operations/events/outbox는 쓰지 않는다.

모델이 기본값을 추가하거나 타입을 정규화해도 raw JSON을 별도 비교한다. 허용한 필드 밖의
원문 전체와 이전 operations는 같아야 한다. 원래 요청 전체를 보존하고 미확정4건을 검사한다.
완료된 요청이 같이 있을 수 있으므로 전체 요청 수를4로 가정하지 않는다. 원문 IDs나 모델
ValidationError 원문은 출력하지 않는다. 알려진 오류 코드와 공개 단계/오류 종류만 기록한다.

독립 SQL 두 번과 Python 검증 두 번이 동일해야 한다. helper를 정상 종료 확인하고 사본 DB도
정상 종료하며 입력 해시를 재검사한다. 전송 무결성/사전 검사 실패는 새 자원 생성 전에 차단한다.
새 타입 검증 성공은 별도 typed_model_review 결과에 기록한다. SQL 초안의 기존 typed=false는
SQL 단독 단계의 표시를 보존한다. 실제 commit payload, CAS/lease/event/outbox 트랜잭션,
source 수락, 기준금액 채택, 7일 제한 예외, 과거 요청 해소, 자정/최초 자금 증명 또는 거래 재개는 없다.

테스트는 합성 원장, 실제 PostgreSQL과 고정89개가 일치하는 로컬 API 이미지에서 시행한다.
비특권·RO socket·networknone helper 및 PowerShell의 실제 생성/검사/payload/출력 경로를
검증한다. 사용자 Windows5.1/NTFS/원본 파일 검증은 실제 결과를 관측하기 전까지 미검증이다.
