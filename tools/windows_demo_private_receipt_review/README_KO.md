# v3 성공 영수증 읽기 전용 검토

바탕화면에 압축을 풀고 CMD에서 RUN_RECEIPT_CHECK.cmd를 실행합니다. Docker는 켜거나
조작할 필요가 없습니다. 원본·기존 사본 정지와 USB 분리 상태를 유지합니다.

사용자 성공 화면의 16일자 v3 폴더와 소스 버전으로 기록을 한정합니다. 성공 영수증,
PRIVATE_WEEK_RESULT.json, PRIVATE_RESOURCES.json, RESTORE_RESOURCES.json,
CLONE_CREATED.json 다섯 파일을 읽고 내용과 식별자·기간·카운터·합성 검토 범위를 대조합니다.
101.004초와 Decimal 진단, 서명 직전 lease/75초 제한, 키 소멸·정지 기록도 확인합니다.
파일은 read sharing으로 열고 검사 끝에 해시를 다시 확인하며, 어떤 파일도 고치거나 생성하지
않습니다. 결과와 각 파일의 SHA256은 콘솔에만 출력합니다. 키 파일이나 응답 원문은 읽지 않습니다.

성공 표시는 PRIVATE_WEEK_V3_RECEIPTS_VERIFIED_NO_WRITES_NO_DOCKER입니다.
출력 전체를 보내주세요. 실제 현재 컨테이너 상태·원문·거래소 신원·외부 보관 해시를
독립 검증한 것은 아니며 기간 채택이나 거래 승인을 뜻하지 않습니다. 별도 최신 해시가
고정돼 있지 않으므로 receipt_hash_compared_to_external_pin은 false로 남습니다.
