# 보존 DB의 새 복사본에서 요청 이벤트 필드 조회

사용자는 no-send 검사 성공 화면 후 다음 단계 진행을 요청했다. 보존 스냅샷의 선택된 요청4에는
미발송 이유가 없었다. 현재 평탄 상태나 조회 not-found를 미발송 증명으로 바꾸지 않는다.
이벤트 이력은 runtime JSON이 아니라 database.tar의 PostgreSQL 자료 안에 있으므로,
원본/기존 복구 사본을 정지 상태로 유지하며 별도의 새 복사본만 복구·기동·정지하는 검사를 준비했다.

## 재사용하는 보호 경계

기존 copy-transition runner의 원본/복구 영수증 pin, 입력 해시, 정지 범위·이미지·외부 볼륨 사용자,
원본 snapshot, 새 볼륨과 helper ID, archive byte/metadata 복원, graceful stop 검사를 재사용한다.
CONTEXT/SUPPORT/SOURCE_SCOPE/restore_worker/cold_archive/account_check는 바이트 동일하며
COMMON은 새 보고서 prefix만 바꾼다. COPY_CORE의 Docker start/stop/restore/원본검사 명령 AST는 동일하다.
원본 또는 이전 복구 볼륨은 helper에 RW로 마운트하지 않는다. 실제 recovery 쓰기는 새 DB 볼륨에만 있다.

새 database/runtime/socket 볼륨과 network=none/pull=never/restart=no/read-only root helper만 사용한다.
읽기 helper는 새 runtime/socket을 readonly로 마운트하며, API/거래 서비스를 시작하지 않는다.
원본/기존 사본은 계속 정지 상태다. 정상 조회 후 새 DB를 정지하고 source snapshot과 입력 해시를 재검사한다.
정상/실패 보고서와 새 정지 리소스는 보존하며 자동 정리·자동 재시도는 하지 않는다.

## 조회 범위와 한계

기준 이미지 소스89와 메모리 후보2(기존 historical_receipts projection/새 copy_event_review)를 해시 검사한다.
코드를 설치하지 않는다. DB 세션은 READ ONLY이고 현재 원본 DB에 연결하지 않는다.
복구 DB의 계정/기간/주문34/미해결4 및 runtime의 원래 요청 계획 SHA256/역할/상태를 연결한다.
기존 projected_timelines를 그대로 사용해 네 요청 필드만 단일 서버 커서로 읽는다.
4096행/원본512MiB·행2MiB/전송128MiB·행2MiB/60초·SQL2초/요청별전이128개의 제한을 유지한다.
원본 payload 전체를 전송하거나 full-content를 검증했다고 주장하지 않는다.

전체 projection은 비공개 EVENT_TIMELINES.json, 콘솔은 네 요청의 전이 개수/시각순 첫·마지막 상태/
미발송 이유 전이 개수만 표시한다. source-row coverage와 full event-content 검증을 구분한다.
as_of,event_id 정렬은 저장 시각순이며 실제 실행·네트워크 발송 순서를 입증하지 않는다.
NOT_SENT와 이유가 보이더라도 저장된 로컬 전이이며 독립 미발송·원격 해소 증명이 아니다.
감사 체인/전체 발송 기록을 replay하지 않으며 요청 해소·재전송·기준값/날짜 채택·거래 권한은 false다.

## 실행 안내

ZIP 바탕 화면 압축 해제 → RUN_CHECK.cmd → 검사 성공 시 RUN_EVENT_REVIEW.cmd 한 번.
원본/기존 사본 정지, USB 분리를 유지한다. 키 입력이나 실제 거래소 조회는 없다.
현재 단계는 새 격리 복사본의 DB recovery와 새 로컬 보고서 쓰기를 포함하며, 완전 무기동/무쓰기
runtime JSON 검사와 구분한다. 새 DB 복사본의 clone_stopped와 input hashes 재확인 결과를 확인한다.
실패 시 retained IDs/볼륨/정지 상태를 검토하며 임의 재실행하지 않는다.
