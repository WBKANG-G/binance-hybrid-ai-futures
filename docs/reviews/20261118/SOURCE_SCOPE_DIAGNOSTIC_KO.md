# 원본 범위 검사 차단 진단

사용자 Windows 화면에서 RUN_CHECK와 이어 실행한 RUN_COMPARE가 모두 INPUTS에서
RESTORE_SOURCE_SCOPE_CHANGED로 차단되었다. retained_helper_id=null, 원본 시작/쓰기 false,
HTTP/주문0이다. 실제 물리 파일 비교는 시작하지 않았다. 이는 데이터 손상 증거도 아니며
원본이 정상적으로 대조되었다는 결과도 아니다.

이전 도구 catch에서 이미 존재하는 RestoreScopeProbe를 출력하지 않은 결함을 확인했다.
실제 불일치 항목은 아직 알 수 없다. 개수 차이, API/DB 선택, mount/image 관계,
저장 snapshot과의 차이 등 같은 오류를 내는 위치가 여럿이므로 추정으로 조건을 바꾸지 않는다.

새 패키지는 기존 입력/구성 검사 함수를 동일 byte로 고정 재사용하고 안전한 probe만
출력한다. step은 고정 enum, 개수는 제한된 정수 범위, 역할은 고정 enum, 일치 여부는
bool/null, 필드명은 고정 목록으로 제한한다. ID·경로·원문·임의 오류 문자열은 출력하지 않는다.
Docker create/start/stop/update/rm이나 비교 worker를 포함하지 않는다. 진단 완료 marker는
원본 검사가 통과했다는 뜻이 아니며 failure_category와 scope_probe를 함께 보아야 한다.

원본 비교 재실행을 지시하지 않는다. 새 진단 결과를 확인한 뒤에만 실제 원인을 판단한다.
기준금액/요청 상태/서비스/주문에 변경이 없고 새로운 실행 승인도 하지 않는다.
