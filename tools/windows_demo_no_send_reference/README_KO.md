# 과거 요청의 로컬 발송 안 함 기록 확인

ZIP을 바탕 화면에 압축 해제하고 RUN_NO_SEND_CHECK.cmd를 실행하세요.
USB 연결·Docker 시작·원본/복구 사본 시작·API 키 입력은 필요하지 않습니다.
실행 결과 문구는 명령어가 아닙니다.

고정된 manifest.json과 runtime.tar의 해시/크기를 검사하고 읽기 전용 스트림을 유지합니다.
날짜13 채택 이전 old_row.state, 과거 original-ID 조회 계획과 완료 보고서, exit 후속 보고서만
허용된 archive 경로에서 tar -xOf로 메모리에 읽습니다. 디스크 압축 해제·파일 변경은 없습니다.

요청4/주문34/DEMO 계정 및 역할·상태·요청 해시·유일 주문 연결은 이전 성공 reader와 동일하게
검사합니다. request_no_send_evidence에서 해당 요청 해시에 연결되는 항목만 확인하고,
정해진 이유 3개(LOCAL_VALIDATION_FAILED/POSITION_ALREADY_FLAT/POSITION_DIRECTION_MISMATCH)
또는 기록 없음만 출력합니다. 원래 Client Order ID·계정 원문·키·응답 원문은 표시하지 않습니다.

이 값은 과거 스냅샷에 저장된 주장입니다. 이번 검사는 현재 DB·요청 당시 이벤트 전이·네트워크
발송 기록을 재검증하지 않습니다. 기록이 없으면 발송 여부를 판별할 수 없습니다.
기록이 있어도 UNKNOWN/SUBMITTING 상태와 함께 남아 있다면 별도 검토가 필요하다고 표시하며
NOT_SENT로 바꾸지 않습니다. 빈 map은 발송 안 함 증명이 아닙니다.
NOT_FOUND_UNRESOLVED와 현재 포지션0을 미발송 증거로 바꾸지 않습니다.

요청 해소·재전송·원장 변경·기준금액 채택·거래 재개 권한은 계속 false입니다.
조회 상태가 FILLED로 기록되어 있어도 이 검사 자체가 독립 원격 증명은 아닙니다.
실제 거래소 요청·키 접근·주문·서비스 시작·자동 재시도는 없습니다.
원본/복구 사본 정지와 USB 분리를 유지하고 결과 화면을 확인하세요.
