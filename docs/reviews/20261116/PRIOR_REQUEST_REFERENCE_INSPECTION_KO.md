# 보존 원장 사본의 과거 요청 대조 정보

사용자 Windows 콘솔에서 고정 manifest SHA256과 runtime archive SHA256 검사를 통과한 뒤
과거 original-ID 조회 보고서와 exit 후속 보고서의 요약을 읽은 결과가 관찰되었다.
original 보고서: 진입2 FILLED/identity_fields_match=true, 손절1 NOT_FOUND_UNRESOLVED,
조회3/미조회1. 후속 보고서: EXIT1 NOT_FOUND_UNRESOLVED, 조회1/미조회0.
전부 remote_resolution_verified=false이다. 이는 과거 결과의 재열람이며 새 HTTP 조회가 아니다.
NOT_FOUND는 취소·체결·미전송을 입증하지 않는다. 전체 요청 상태는 계속 미해결로 취급한다.

웹 화면에서 BTCUSDT 데모 포지션0·Basic0·Conditional0과 Single-Asset Mode,
잔고4998.7418/미실현손익0 표시가 관찰되었다. 현재 웹 계정이 프로그램용 키 발급 계정과 같다는
사용자 진술은 받았지만 독립 계정-키 연결 증명으로 승격하지 않는다.
수집한 화면은 일부 행과 UI 범위이며 전체 이력·UTC 시간대·기준액을 입증하지 않는다.

확인한 소스 scripts/adopt_demo_accounted_day13.py는 기존 DB row를 old_row에 저장한다.
따라서 database.tar를 새로 복구하지 않고 고정된 runtime.tar 안의 adoption intent의
old_row.state에서 이전 원장 요청4/주문34의 대조 정보를 읽을 수 있다.
현재 데이터베이스가 아니며, 실제 날짜 채택 또는 원장 변경을 수행하지 않는다.

새 도구는 원본 manifest pin과 runtime archive hash/size를 확인하고 읽기 전용 스트림을 유지한다.
허용된 두 intent만 tar -xOf로 메모리에 읽으며 디스크 추출을 하지 않는다.
과거 adoption policy/account, original-ID policy/account, 계획의 요청 SHA256/역할/상태,
주문34/미해결4/진입2·손절1·종료1, 요청별 유일 주문을 검사한다.
표시는 화이트리스트로 제한한다. raw Client Order ID·키·원문 state/응답은 출력하지 않는다.
서버 번호는 양의 정수 문자열만 허용한다. EXIT에 없는 서버 ID를 ENTRY 번호로 대신하지 않는다.
entry_server_order_id는 관련 진입 주문번호로 별도 표시한다.
신호 기준 UTC 캔들 시각은 주문/체결 시각이 아니다.

사용자 실행: ZIP 압축 해제 후 RUN_INSPECT.cmd. USB 연결·Docker 시작·원본/복구 사본 시작 불필요.
네트워크·주문·데이터 변경·자동 재시도 경로는 없다.
수집 결과로 과거 미해결 요청 해소/기준액 수락/설치 준비/실행 승인/거래 재개를 자동 처리하지 않는다.
