# 저장 영수증 구조 읽기 전용 검사

기존 RUN_EVIDENCE_REHEARSAL.cmd를 다시 실행하지 않습니다. USB는 분리하고 기존 컨테이너는 정지 상태로 유지합니다. 이 검사기는 Docker를 호출하거나 새 복사본을 만들지 않으며 파일을 수정·복구하지 않습니다.

RUN_RECEIPT_CHECK.cmd를 한 번 실행합니다. 내부 백업 폴더에서 고정된 코드 참조의 성공 영수증을 하나만 찾아 COPY_EVIDENCE_RECEIPT.json, COPY_CLOCK_RESULT.json, COPY_EVIDENCE_RESULT.json을 읽습니다. 저장된 content_review가 객체인지, remaining_requirements가 8개 항목의 배열인지, 세 파일의 중첩 데이터가 같은지 검사합니다. 기존 일/주 기준·요청을 채택하거나 진술/원천 수락을 새로 만들지 않습니다.

성공 표시는 EVIDENCE_RECEIPT_STRUCTURE_VERIFIED_NO_WRITES_NO_DOCKER입니다. 출력된 receipt_sha256은 읽은 영수증의 현재 해시이며, 이 검사 자체로 외부 보관 해시와 대조한 것은 아닙니다. 현재 컨테이너 상태, 원격 계좌 신원 또는 응답 원문의 거래소 진위를 검증하지 않습니다.

콘솔의 @{...} 문자열을 파싱해 객체로 복구하지 않습니다. 저장 파일 자체가 문자열이거나 손상됐다면 실패하고 원본을 보존합니다. 실패 시 기존 합성 실행기를 반복하거나 파일을 편집하지 말고 출력 JSON을 확인합니다.
