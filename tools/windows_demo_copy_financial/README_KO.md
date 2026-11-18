# 새 격리 사본 SQL 재무 대조

RUN_CHECK.cmd 성공 후 RUN_READ_FINANCIAL.cmd를 한 번 실행한다. 실패하면 다음 명령을 실행하지 않는다.
원본 DB/API/키 세션과 기존 사본·보조 컨테이너는 수동 시작·삭제하지 않는다. API 키·USB는 필요 없다.

고정 백업·성공 복원 영수증·기존 원본/보조 컨테이너 구성을 검증한다. 앞선 원본 비교의 보조
컨테이너는 사용자 화면에 보고된 정확한 ID 하나만 허용하며, 캐시 이미지·RO mount 두 개·
networknone·rootRO·cap·명령·중지·정상종료 검사를 매번 통과해야 한다. 이름/label/역할만으로 제외하지 않는다.

새 DB/runtime/socket 볼륨과 새 복원 helper/DB 컨테이너를 만들고 고정 백업에서 복원한다.
사본에만 PID 정리/WAL 복구가 발생할 수 있다. 사본 DB는 네트워크 없음·SQL 기본 read-only,
비활성 archive/restore hooks·사전 적재 없음으로 실행한다. 원본 볼륨은 사본에 마운트하지 않는다.
읽기 전용 repeatable-read SQL을 두 번 실행해 원장 지문·재무 항목·고점·가용금액·평가시각·
해시화 요청상태를 대조한다. 복원 원장 지문이 고정 복원 보고서와 같아야 한다.
재무 수치와 요청 해시는 저장된 금융/채택 기록 및 원래 요청 계획과도 대조한다.

실제 SQL로 읽은 금융 값이 출력되지만 현재 원본 SQL 조회나 전체 새 원장 전이 검증은 아니다.
자정 평가액/최초 자금 증명/기간 source 수락/7일 초과 적용 경로/과거 요청 해소/거래 재개는 별도다.
원장·기준금액·요청 상태를 바꾸는 SQL, HTTP, 키 입력·LIVE·주문 기능은 없다.

성공: COPY_FINANCIAL_READ_COMPLETE_ORIGINALS_UNCHANGED_COPY_STOPPED_NO_ORDERS
완료 후 사본 DB는 정상 종료하고 새 볼륨·helper·개인 영수증은 보존한다. 자동 삭제·재시도는 없다.
실패하면 retained IDs와 clone_stopped 결과를 확인하고 화면만 공유한다. 원본 파일·영수증 원문·키를 업로드하지 않는다.
