# 콜드 백업 v2: 내부 계획 전달과 조회 전용 진단

사용자 화면은 `FRESH_READ_ONLY_PLAN / COLD_FRESH_PLAN_BLOCKED`를 보고했다. 정지 시도·이미지 저장·백업 생성은 없었으며 보관 디렉터리는 null이었다. 실제 Windows의 내부 계획 결과는 원격으로 읽지 않았다. 이 화면만으로 별도 프로세스 실행 실패, 출력 형식 차이, 실제 계획 차단 중 무엇인지 확정할 수 없다.

## 수정한 구조

기존 실행기는 별도의 powershell.exe/pwsh 프로세스에서 계획 스크립트를 실행하고 종료 코드·정확히 세 줄의 텍스트를 검사했다. 어떤 조건이 실패해도 같은 `COLD_FRESH_PLAN_BLOCKED`를 표시해 내부 단계/원인을 가렸다.

v2는 hash로 고정된 계획 스크립트를 **같은 PowerShell 프로세스**에서 `-ReturnPlanObject`로 호출한다. 이 모드는 Console 문자열·줄 수·자식 프로세스 종료 코드에 의존하지 않고 성공/차단 객체 하나를 반환한다. 단독 계획 명령의 기본 3줄 출력과 종료 코드는 유지한다. PG 경로·버전·식별자·볼륨·외부 소비자·원본 scope·pause/lease·종료 조건은 완화하지 않는다.

`fresh_plan`에는 허용된 상태·단계·실패 코드·PG probe 상태만 복사한다. 예상 외 출력, 여러 객체, 안전 플래그 true, 허용하지 않은 probe 값/단계/코드, 예외 원문은 차단하고 반환하지 않는다. 원본 JSON/예외/환경변수/비공개 문자열을 콘솔에 내보내지 않는다. 실제 계획 차단은 `PLAN_BLOCKED`, 유효한 객체가 없으면 `NO_VALID_PLAN_RESPONSE`로 구분한다.

## 먼저 실행하는 명령

v2 ZIP의 `RUN_CHECK.cmd`는 `-CheckOnly`를 사용한다. 새 계획·검토된 컨테이너/볼륨 범위·키 세션 실행 상태·pause/lease·주문/미해결 개수·원본 구성 불변을 검사한다. 소스 SQL은 READ ONLY이며 키 세션 내용은 읽지 않는다.

이 모드는 mutex/백업 디렉터리 생성·NTFS ACL 변경·이미지 저장·컨테이너 정지·helper/볼륨 생성·백업 복사 이전에 반환한다. 성공 문구는 `COLD_BACKUP_CHECK_PASSED_NO_STOP_NO_BACKUP_NO_ORDERS`다. NTFS ACL이나 실제 백업 준비 완료를 증명하지 않는다.

`RUN_BACKUP.cmd`는 여전히 실제 정지·백업 명령이다. `-ExecuteColdBackup`과 `-CheckOnly`를 동시에 지정하면 거부한다. 원본을 자동 재시작하거나 정지 정책을 변경하지 않는다. 사용자에게 우선 RUN_CHECK 결과를 요청하고, 실제 백업 실행은 그 결과를 검토한 뒤 진행한다.

## 검증과 제한

객체 전달 성공/차단, 알려진 내부 원인 표시, 비공개 코드·단계·probe·추가 출력·예외·안전 플래그 주입 거부를 검사한다. 실행기 조회 모드가 폴더·이미지·helper를 만들거나 정지 신호를 보내지 않는지 검증한다. 기존 단독 계획 명령의 동작도 회귀 검사한다.

실제 Docker 시험은 합성 6개 대상과 키 세션, 새 internal 네트워크/볼륨만 사용한다. 동일 실행기의 조회 모드가 원본 API/DB를 계속 실행 상태로 유지하고 출력 폴더를 만들지 않는지 확인한 뒤, 실제 백업 모드·새 볼륨 복원을 시험한다. 기간 키와 감사 테이블의 보존을 확인한다. Windows NTFS ACL 함수는 Linux 시험용 복사본에서 대체하며 production ZIP에는 우회 코드가 없다.

최종 수치와 source/ZIP hash는 artifact VALIDATION.json에 기록한다. 실제 Windows에서의 내부 원인과 권한/백업 성공은 아직 확인 전이다. 실제 데이터 파일은 업로드하지 않고 콘솔 JSON과 종료 문구만 공유한다.
