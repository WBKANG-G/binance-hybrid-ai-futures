# v3 성공 영수증의 읽기 전용 검토

사용자 성공 화면의 소스 a70f103b3a5cd764ea43838d9a6e565637dafa32와 폴더 copy-private-week-v3-20261116-57445e871754fd497063f55423bddae0로 대상을 한정한다. 새 영수증의 해시는 화면에서 다시 전사하거나 외부 pin으로 간주하지 않는다. 성공 영수증과 PRIVATE_WEEK_RESULT.json, PRIVATE_RESOURCES.json, RESTORE_RESOURCES.json, CLONE_CREATED.json 다섯 파일의 로컬 내용 일치를 검사한다.

검사 대상은 이전 성공 영수증/보존 해시 참조, source/version, 55→56→58 감사 카운터와 58→59 시계 revision, 284 시계 전후 weight, 준비 101.004초/Decimal 진단/서명 직전 lease/75초 제한, 기간 13일·주간9일 보존과 현재16일·주간16일, 합성 sender 1/HTTP·주문 0, 34개 주문·4개 미해결 요청, 키 소멸·정상 정지 기록, 5개 helper/3개 private helper/3개 clone volume와 private key volume 사이 참조다. 권한/채택/원격 신원/실행 승인 false 및 남은 8개 근거 배열도 검사한다.

파일은 FileMode.Open/FileAccess.Read/FileShare.Read로 열고 1MiB로 제한한다. UTF8 오류/잘못된 JSON/객체 대신 문자열/숫자 대신 문자열/false 대신 true·권한/참조 불일치/빠진 동반 파일/reparse는 거부한다. 객체 키 순서는 정렬해서 구조적으로 비교하고 배열의 의미 있는 순서는 유지한다. 검토 끝에 열린 stream의 길이와 SHA256을 재확인한다. 파일 쓰기·복구·Docker·키 파일·응답 원문 조회·컨테이너 시작이 없다. 콘솔에 대상 다섯 파일의 현재 해시를 표시한다.

일치하는 로컬 기록과 자체 해시를 읽는 것이며 외부 독립 해시 pin과 비교하지 않는다. 현재 Docker 상태, 실제 source/backup bytes, 원문 재생·서명·거래소 신원·별도 PC 검증도 수행하지 않는다. receipt_hash_compared_to_external_pin/current_docker_state_checked/archive_response_bytes_replayed는 false로 남기고, 채택·실행·주문 승인도 false다. 현 단계는 읽기 전용 검토 패키지 전달이며 사용자 실행 결과는 아직 관측하지 않았다.

네이티브 PowerShell 검사에는 유효 기록과 손상/승인 오표시/원본 시작 주장/기간·카운터 오류, 동반 결과 차이, 순서만 다른 객체의 허용, seal 변조, Decimal/Double 및 잘못된 숫자형, 종료 시 길이·해시 재검사를 포함한다. 길이·해시 변경 주입은 가짜 메모리 stream으로 하며 실제 시험 파일도 변경하지 않는다. 명령 AST로 Docker·외부 프로세스·파일 쓰기 명령/메서드가 없는지 검사한다. 실제 Windows 파일을 읽은 것으로 표현하지 않는다.
