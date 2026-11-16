# 참조 파일 USB 복사 — 2026-11-16

이 패키지는 로컬에서 이미 만든 해시 참조 JSON 2개를 D: USB의 새 폴더로 복사합니다.
기존 복구 백업을 새로 만들거나 덮어쓰지 않습니다. 원본·복구 사본은 정지 상태로 유지하세요.

1. 사용하던 USB를 연결하고 탐색기에서 D:인지 확인합니다. 다른 문자라면 실행하지 마세요.
2. ZIP을 바탕 화면에 압축 해제합니다. 이 README가 있는 폴더에서 RUN_CHECK.cmd를 실행합니다.
3. `REFERENCE_USB_CHECK_PASSED_NO_WRITES_NO_DOCKER`가 나오면 RUN_HANDOFF.cmd를 한 번 실행합니다.
4. `REFERENCE_USB_COPY_VERIFIED_SAFE_EJECT_PENDING_NO_DOCKER`와 JSON을 확인합니다.
5. Windows에서 USB를 안전하게 제거한 뒤 물리적으로 분리하고 별도로 보관합니다.

생성 위치: D:\WBKANG-DEMO-Reference-20261116-<새 UUID>\
파일: REFERENCE_MANIFEST.json, REFERENCE_RECEIPT.json, REFERENCE_HANDOFF_RECEIPT.json.
앞의 2개는 원본과 SHA256을 비교하고, 마지막 1개는 이번 복사 기록입니다.

D:가 USB이며 부팅/시스템 디스크가 아니고 쓰기 가능하며 NTFS 또는 exFAT인지 검사합니다.
장치 식별값을 반복 비교합니다. 포맷·기존 파일 삭제·덮어쓰기·자동 재시도는 없습니다.
exFAT은 NTFS 접근 권한 검증을 지원하지 않으며 결과에 false로 표시합니다.
실패하면 중간 폴더를 남기며 다시 실행하지 말고 출력 내용을 확인하세요.

로컬 참조는 제작 소스 ee8aa302e90ec2b9227e03c557b2a226b78f0289와 현재 검사된
V3 성공 영수증 해시가 일치하는 유일한 폴더로 선택합니다. 최신 날짜나 첫 항목을 고르지 않습니다.
원본 영수증 5개를 다시 읽어 검사하고 참조의 정해진 필드와 연결된 해시를 확인합니다.
개인키·실제 API 자격증명·원본 영수증 내용·응답 본문·복구 데이터는 복사하지 않습니다.
Docker·서비스·거래소 요청·주문 실행은 없습니다.

이 단계는 해시 참조의 복사입니다. 별도 PC 검증, 외부 독립 보관 증명, 거래 재개 승인,
8개 미충족 증빙 해소 또는 설치 준비 완료를 의미하지 않습니다.
콘솔의 성공 문구는 실행 명령이 아닙니다. RUN_CHECK.cmd와 RUN_HANDOFF.cmd만 실행하세요.
