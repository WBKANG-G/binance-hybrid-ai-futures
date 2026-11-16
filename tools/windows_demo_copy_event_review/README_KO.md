# 새 격리 복사본의 요청 이벤트 이력 조회

기존 원본·복구 사본·이전 연습 사본은 정지 상태로 유지합니다. USB 연결·키 입력은 필요 없습니다.
이번 작업은 원본 백업에서 새 UUID 볼륨/컨테이너만 만들고, 네트워크 없는 새 DB 복사본을 잠시
기동합니다. DB 복사본의 crash recovery는 새 볼륨에만 일어납니다. 기존 볼륨을 시작/수정하지 않습니다.

ZIP을 바탕 화면에 압축 해제하고 RUN_CHECK.cmd를 먼저 실행합니다.
RESTORE_CHECK_PASSED_NO_CLONE_NO_SOURCE_START_NO_ORDERS가 나오면 RUN_EVENT_REVIEW.cmd를 한 번 실행합니다.
검사에서 차단되면 실행을 이어가지 말고 출력 화면을 확인하세요. 출력 문구는 명령어가 아닙니다.

원본 보존/복구 영수증·입력 해시·정지 범위·이미지 ID·외부 볼륨 사용자를 검사합니다.
새 database/runtime/socket 볼륨만 생성해 검증된 archive byte/metadata를 복원합니다.
모든 helper는 network=none, pull=never, restart=no, read-only root, 제한된 메모리/PID로 실행합니다.
API/거래 서비스는 시작하지 않고 Python 읽기 helper만 새 runtime/socket을 읽기 전용으로 사용합니다.
기준 이미지 소스89와 메모리 후보2를 해시 검사하며 후보를 설치하지 않습니다.

복구된 DB의 계정/기간/주문34/미해결4와 원래 조회 계획 해시/역할/상태를 연결합니다.
모든 DB 세션은 READ ONLY입니다. 기존 projection 함수로 네 요청의 필드만 단일 서버 커서에서 읽습니다.
원본 이벤트 payload 전체·Client Order ID·키·응답 본문은 출력하지 않습니다.
범위 제한: 4096행, 원본 처리512MiB/행2MiB, 전송128MiB/행2MiB, 60초 및 SQL2초,
요청별 상태 전이128개. 제한/자료 오류 시 원문 노출·재시도 없이 차단합니다.

전체 projection 결과는 새 비공개 보고서 폴더의 EVENT_TIMELINES.json에 저장됩니다.
콘솔에는 네 요청별 상태 전이 개수·저장 시각순 첫/마지막 상태·미발송 이유 전이 개수만 표시합니다.
시각순은 as_of,event_id 정렬이며 실제 실행/네트워크 발송 순서를 입증하지 않습니다.
현재 원본 DB·감사 체인·실제 발송/미발송·원격 해소를 검증하지 않습니다.
저장된 NOT_SENT 이유가 나와도 독립 미발송 증명이나 요청 해소 권한으로 자동 승격하지 않습니다.

조회 후 새 DB 복사본을 정상 정지하고, 원본 상태와 입력 해시를 다시 확인합니다.
새 볼륨/정지 helper/보고서는 추후 검토를 위해 남깁니다. 자동 정리·자동 재시도·원본 재개는 없습니다.
실패 시 retained_helper_ids/retained_clone_volumes/clone_stopped를 확인해야 하며 임의 재실행하지 마세요.
이번 새 복사본의 내부 recovery와 로컬 보고서 쓰기 외에는 금융 원장·요청 상태·기준액을 바꾸지 않습니다.
거래소 HTTP·키 접근·주문·날짜 채택·설치·실행 승인·LIVE는 모두 비활성입니다.
