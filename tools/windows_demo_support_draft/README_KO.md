# 두 요청 확인을 위한 비공개 문의 초안

RUN_CHECK.cmd는 고정된 과거 백업과 두 요청의 연결을 읽기만 합니다.
성공하면 RUN_CREATE_DRAFT.cmd를 한 번 실행합니다. 새 비공개 폴더에
REQUEST_CLARIFICATION_KO.txt와 DRAFT_RECEIPT.json만 생성합니다.
콘솔의 private_draft_file 경로를 파일 탐색기에서 열어 초안을 확인하세요.

초안에는 손절·종료 요청의 원래 client ID 두 개가 들어갑니다. API Key/Secret은
접근하지 않습니다. 초안 내용을 채팅·공개 게시판에 올리지 마세요. 확인 후 필요하면
로그인한 공식 비공개 고객지원 채널에 사용자가 직접 전달합니다. 자동 전송은 없습니다.

저장 위치는 LOCALAPPDATA 아래 WBKANG-DEMO-Evidence-Requests의 새 폴더입니다.
Windows NTFS에서 현재 사용자와 SYSTEM만 허용하는 보호된 ACL을 요구합니다.
기존 폴더 ACL이 다르면 수정하지 않고 중단합니다. 덮어쓰기·자동 재시도는 없습니다.
실패 시 부분 생성물이 남을 수 있으며 retained_partial_draft_directory로 표시합니다.

백업은 day13 채택 이전의 과거 스냅샷이며 현재 데이터베이스가 아닙니다.
NOT_FOUND, 취소된 진입 주문, 현재 포지션 0은 두 요청의 미전송을 증명하지 않습니다.
거래소의 현재 보관 정책을 독립 확인하지 못했으므로 문의에서 조회 한계를 묻습니다.

원본·기존 복사본은 계속 정지하고 USB는 분리해 두세요. Docker·서비스 실행,
HTTP 요청, 요청 해소·재전송, 기준금액 변경, 실거래는 수행하지 않습니다.
클라우드 검증은 합성 자료와 PowerShell 7 기반입니다. 실제 Windows PowerShell 5.1의
비공개 ACL 생성은 사용자 PC 실행 결과로 확인해야 합니다.
