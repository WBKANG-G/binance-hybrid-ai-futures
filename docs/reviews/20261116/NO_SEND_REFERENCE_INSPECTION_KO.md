# 미해결 요청의 과거 발송 안 함 기록 검사

사용자는 수수료 처리 설명 후 다음 단계 진행을 요청했다. 이번 범위는 보존된 원장 사본에
요청별 미발송 이유가 남아 있는지, 기존 조회 결과와 혼동 없이 확인하는 읽기 전용 검사다.
원본/복구 사본 시작, DB 연결/복구, USB 연결, 실제 GET/POST는 포함하지 않는다.

services/execution/demo.py의 보호 주문 effect는 submission_attempted를 POST 직전에 true로
설정한다. 로컬 검증에서 중단되면 NOT_SENT/LOCAL_VALIDATION_FAILED를 기록하지만, POST를
시도한 뒤의 오류는 UNKNOWN으로 남긴다. 종료 effect는 현재 포지션0/방향 불일치일 때 POST 전에
NOT_SENT와 POSITION_ALREADY_FLAT/POSITION_DIRECTION_MISMATCH를 기록한다.
과거 UNKNOWN 요청을 조회 not-found나 현재 포지션0만으로 NOT_SENT로 바꾸지 않는다.
기존 tests/integration/test_demo_request_no_send.py도 이 경계를 명시한다.

새 reader는 Windows에서 성공한 f3f8e44 core를 그대로 재사용한다. 고정 manifest/runtime hash,
readonly stream, archive 크기/경로 제한, DEMO 계정/주문34/미해결4, 원래 계획과 요청 해시/역할/상태/
유일 주문을 검사한다. adoption intent, original-ID intent/complete, exit 후속 complete만 tar -xOf로
메모리에 읽는다. 보고서 정책/작업번호/요청별 관측 연결을 확인하며 원문 계정/Client ID/키를 출력하지 않는다.

request_no_send_evidence가 누락된 과거 스냅샷과 존재하지만 비어 있는 map을 구분한다.
선택된 요청에 연결된 이유는 정해진 세 문자열만 허용한다. 알려지지 않은 이유·null·bool·object는
원문 출력 없이 차단한다. 이유가 있는데 요청 상태 UNKNOWN/SUBMITTING이면 상태 불일치의
별도 검토가 필요하다고 표시한다. 실제 미발송을 확정하지 않는다.

모든 결과는 날짜13 채택 이전 스냅샷과 당시 조회 보고서에 한정된다. 현재 DB·요청 시점의 전체
이벤트 전이·네트워크 발송/미발송·로컬 이유의 독립 진위 검증은 수행하지 않는다.
map/항목 없음은 미발송 증거가 아니며 NOT_FOUND_UNRESOLVED도 미발송/취소/해소 증거가 아니다.
표시된 이유가 있더라도 원래 요청을 재분류·해소·재전송하거나 주문 권한을 켜지 않는다.

사용자 실행 명령은 RUN_NO_SEND_CHECK.cmd 하나다. 결과 문구/계산 설명은 명령이 아니다.
파일 변경·디스크 압축 해제·거래소 요청·키 접근·Docker/서비스 시작·자동 재시도는 없다.
원본/복구 사본 정지 및 USB 분리를 유지한다.
