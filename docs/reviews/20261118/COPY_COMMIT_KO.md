# 새 사본에서 원장·이벤트·대기열 COMMIT 리허설

사용자 화면에서 ADOPTION_MODEL_REHEARSAL_COMPLETE_COPY_STOPPED_NO_ADOPTION_NO_ORDERS를
확인했다. 완료/후보 모델 검증, COPY_REHEARSAL_ONLY 기록의 실적용 거부, 원문 보존과 사본
종료가 보고됐다. 사용자 원문 파일을 클라우드에서 읽은 결과는 아니다. 이번 다음 단계 지시는
새 폐기 가능 사본의 트랜잭션 검증으로 수행하며 실제 원본 수락/적용으로 확대하지 않는다.

고정3d938f6 패키지의 백업 검증·정지 범위 검사·새 볼륨 복원·사본 생성·읽기 전용 모델 검증을
그대로 수행한다. 추가 helper에서만 실제 SQL 저장 리허설을 한다. helper 시작 전에 새 clone이
이번 실행의 Owned ID이며, 이미지/2개 마운트/networknone/실행 상태가 맞고, socket이 바로
그 clone의 새 socket이며, 이전 Original 컨테이너 어느 것도 사본 볼륨/socket을 쓰지 않는지
확인한다. helper는 socket 하나만 RO 마운트하고 rootRO/networknone/UID10001/capALL제거를
유지한다. 원본의 DB 볼륨/서비스/API/키 세션에는 접근하거나 시작하지 않는다.

새 worker는89개 설치 코드·검증 모듈·SQL·읽기 worker 해시를 확인하고 기존 READ ONLY 모델
검증을 먼저 두 번 수행한다. 이어 별도 연결에서 원문/지문/버전을 묶는다. 연결 기본값은 READ
ONLY이며 해당 사본 트랜잭션만 READ WRITE로 바꾼다. 서버 기본 설정은 변경하지 않는다.
행 잠금/2초 잠금 제한/5초 statement 제한,110 버전과 원문 SHA 조건을 검사한다.
유효 lease는 거부한다. 오래된 owner가 복원된 경우 만료 lease/NULL 여부를 확인한 뒤 사본에서만
fence 증가와 함께 owner/lease를 해제한다. 이 동작을 원본의 권한 인수 정책으로 적용하지 않는다.

변경 후보는11/18·주11/16·동일 일/주 기준4998.74183608·reconciled/stream_ready=false와
COPY_REHEARSAL_ONLY 감사 문자열1건이다. 원문 JSONB를 Python으로 다시 직렬화하여 갱신하지
않고 SQL jsonb_set/병합으로 허용 필드만 바꾼다. 기존34주문/전체 요청과 미확정4건/고점5000/
평가액 구성과 시각/이전 감사 기록/다른 원문 값을 보존한다. 별도 타입 모델도 검사한다.

copy의 public.demo_accounts, demo_events, demo_outbox를 한 트랜잭션에서 갱신한다.
이벤트 ID=가상 operation UUID, kind=DEMO_COPY_PERIOD_REHEARSAL, outbox topic=DEMO_AUDIT다.
새 진입/취소/재전송 작업이나 네트워크 효과를 만들지 않는다. 사본 소비자는 시작하지 않는다.
가상 완료 기록은 copy 전용 context와 재현 가능한 테스트 지문/1970년 시각을 사용한다. 실제
출처 수락이나 운영 완료 기록이 아니며 실적용 검사는 거부한다. 모형의 applied=true를 운영
수락으로 해석하지 않는다. 원장 전체가 사본 이벤트의 비공개 payload에 들어가므로 사본도
비공개 보관한다. stdout/JSON 영수증은 원문/주문ID 대신 상태·지문·검증 결과만 기록한다.

계정 갱신 직후/이벤트 직후/outbox 직후의 오류3건을 명시적으로 발생시켜 원장 원문, fence,
owner/lease, 이벤트/outbox 수가 모두 시작점과 같은지 확인한다. 이는 불명확한 실제 주문의
자동 재시도가 아니다. 그 다음 정상 COMMIT 후에만 성공을 반환하고, 같은 operation을 다시
호출해 원문 전체·버전·owner/lease·이벤트 payload·outbox가 맞아야 이미 완료로 인정한다.
별도 연결에서 최종 원장/버전과 이벤트/outbox 증가가 각각1인지 다시 확인한다.

COMMIT 후 검증/호스트 단계가 실패하면 사본에는 변경이 남아 있을 수 있다. 성공으로 꾸미거나
자동 재실행하지 않는다. 기존 finally 경로에서 새 사본을 정상 정지하고 생성 자원/실패 보고를
보존한다. 성공해도 사본을 정지하고 백업 해시/원본 컨테이너 상태를 재검사한다.

출력 operation_scope는 FRESH_DISPOSABLE_COPY_ONLY다. account/financial/transition_draft는
BEFORE_COPY_TRANSACTION이며 저장 후 결과는 copy_commit_review에 둔다. copy_database_changed=true,
original_database_changed/original_source_accepted=false다. 사본 일자18과 원본 출처 일자13을
분리해 표시한다. 기존 모델 검증의 copy_sql_read_only=true는 사전 검사 단계에만 해당한다.

클라우드 검증은 합성 원장을 실제 PostgreSQL 영구 테이블(기존 스토어의 열/PK/FK 형태)에 넣어
시행한다. networknone 사본 DB의 데이터/socket 두 볼륨과 비특권 worker, 실제 PowerShell의
clone 입장/마운트 검사/작업/결과 승인까지 실행한다. 타입 테스트와 이전 감사 모델 회귀도 수행한다.
실제 사용자 백업/추가 제약·트리거/Windows5.1·NTFS의 전체 실행은 사용자 관측 전까지 미검증이다.
원본용 저장 도구·출처 수락·신선한 실제 계정 확인·과거 요청 해소·거래 재개는 포함하지 않는다.
기존7일 제한이나 MASTER_SPEC 잠금 조건도 변경하지 않는다.
