# 16일자 합성 키 검토 v3 — Windows PowerShell 5.1 숫자 호환

USB는 분리하고 원본·기존 실패 사본은 정지 상태를 유지합니다. 실제 키 입력은 없습니다.
바탕화면에 압축을 풀고 CMD에서 RUN_CHECK.cmd를 실행합니다.
RESTORE_CHECK_PASSED_NO_CLONE_NO_SOURCE_START_NO_ORDERS가 나온 경우에만
RUN_PRIVATE_WEEK.cmd를 한 번 실행합니다. 70초 이상 대기 뒤 자료 검사가 이어집니다.
성공 표시는 PRIVATE_WEEK_V3_SYNTHETIC_VERIFIED_NEW_COPY_STOPPED_NO_ORDERS입니다.
실패하면 재실행하지 않고 출력 전체를 보내주세요.

PowerShell 5.1이 JSON 소수를 Decimal로 해석하는 경우를 지원합니다. 숫자 문자열·bool·null·
NaN·무한대는 거부하고 준비 시간 0 이상 300초 미만과 서명 직전 lease 여부를 계속 확인합니다.
서명 75초 제한과 시계·같은 파일 검사·만료 후 갱신 금지는 유지합니다.

v1 도우미 5개 및 v2 실패 기록의 도우미 5개를 메타데이터로 확인하고 계속 정지 상태로 둡니다.
v2 실패 기록은 사용자 화면의 reader ID로 한정해 찾으며 로컬 기록과 현재 마운트/역할/이미지를
검사합니다. 이 기록을 외부 독립 해시 증명으로 취급하지 않습니다. 새 사본만 생성하며 이전
실패 사본을 다시 시작하거나 재사용하지 않습니다. 실제 거래소 통신·주문·기간 채택은 없습니다.
