# 원본·백업 파일 비교 v2

이전 비교 도구는 복원 이전 구성(원본7개+보존helper1개)만 처리하여, 이미 검증된 사본 복원
helper가 같은 프로젝트 label을 가진 경우 개수 검사에서 차단되었다. 이번 버전은 고정된
복원 성공 영수증의 컨테이너 ID·사본 볼륨·이미지·중지 상태·네트워크·깨끗한 종료·원본 관계를
검증하는 기존 복원 이후 구성 검사를 재사용한다. 이름/label만 보고 제외하지 않는다.
알 수 없는 추가 컨테이너·원본 볼륨 소비자는 계속 차단한다. /bundle bind는 고정 백업 폴더와도 대조한다.

바탕화면에 풀고 RUN_CHECK.cmd를 실행한다. SOURCE_COMPARE_CHECK_PASSED_NO_SOURCE_START
성공이 나왔을 때만 RUN_COMPARE.cmd를 한 번 실행한다. 실패하면 비교를 실행하지 말고 화면을
확인한다. 기존 컨테이너를 삭제·시작하거나 Docker Desktop을 재시작할 필요가 없다.
API 키·USB D: 연결도 필요 없다.

실제 비교 방식은 이전 버전과 같다. 새 helper 하나만 실행해 원본 DB/runtime을 RO로 읽는다.
networknone/rootRO/restartno, 별도 output volume 없음. 파일 SHA256·메타데이터·집합을 두 번
대조하고 원본 구성과 백업 입력 해시를 다시 검사한다. 원본 DB/API 시작, 파일 수정,
SQL 질의, 기준금액 채택, 미확정 요청 해소, 거래 재개는 하지 않는다.

성공 마지막 줄: SOURCE_BYTES_MATCH_BACKUP_NO_ORIGINAL_START_NO_LEDGER_CHANGE
성공은 물리 파일 대조 결과이며 금융 diff 확정이나 현재 SQL 원장 직접 조회를 의미하지 않는다.
실패하면 자동/수동 재시도하지 않는다. 새 helper는 중지한 채 보존하며 자동 삭제하지 않는다.
