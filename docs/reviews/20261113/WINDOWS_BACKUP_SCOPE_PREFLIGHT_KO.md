# Windows 백업 범위 읽기 전용 사전 검사

앞 단계 [적용·롤백 계획](WINDOWS_AUDIT_DEPLOYMENT_PLAN_KO.md)의 다음 작업으로, 실제 Windows에서 백업 설계에 필요한 범위를 관측하는 독립 패키지를 구현했다. 앱 후보 모듈 9개, V2 스키마, 전송 경로, 기존 핵심 코드를 수정·설치하지 않는다.

## 구현

- 기존 컨테이너 ID·이미지·영속 마운트의 전후 동일성을 검사한다. 마운트 호스트 경로는 메모리에서만 해시한다. 저장 루트 내 중첩 Docker 마운트는 명시적으로 차단한다.
- 해시 고정 ZIP → CMD → Windows PowerShell 문법 → `docker exec -i <고정 ID> python -I -B -` 순서로 실행한다. 작업 코드·설정은 stdin 전달이며 파일 복사와 앱 import가 없다.
- PostgreSQL READ ONLY / REPEATABLE READ, SQL/lock 2초, 연결 3초 제한을 사용한다. `demo_` 접두사에 제한하지 않고 현재 스키마 relation 최대 128개와 함수·트리거·인덱스·제약·교차 스키마 외래 키/상속 개수를 관측한다. 알 수 없는 이름은 해시로 처리하고 객체 정의·이벤트 본문을 반환하지 않는다.
- 계정은 고정된 `DEMO-WINDOWS-ISOLATED-BTC-V1`이며 paused와 활성 lease 유무만 투영한다. paused가 false이거나 형식이 다르면 차단한다. 요청·주문·자금·기간·lease를 변경하지 않는다.
- 파일은 루트의 각 부모부터 `O_DIRECTORY | O_NOFOLLOW` descriptor로 접근하고 metadata만 순회한다. 링크·특수 파일·확인 중 변경·개수/깊이/시간 한도 초과를 거부한다. 파일 본문·SQLite DB·저널을 열지 않으며 개별 이름을 출력하지 않는다.
- worker 출력은 해시 고정된 폐쇄형 스키마로 PowerShell에서 검증한 뒤 출력한다. 추가 속성, 비공개 문자열, 허용하지 않은 상태, 활성화된 안전 플래그, 종료 코드 모순을 거부한다. 예외·stderr 원문·DB URL은 표시하지 않는다.

## 검증과 한계

검증 범위는 새 worker·PowerShell 실행기·번들·합성 PostgreSQL·기존 설치 Docker 이미지 및 기존 인벤토리 회귀 검사다. 실제 READ ONLY 트랜잭션에 UPDATE를 주입해 SQLSTATE 25006 거부와 재무 데이터 불변을 확인한다. V2 테이블·비공개 이름·함수 본문을 가진 fixture, 교차 스키마 의존성, 과도한 객체 수, 위험 파일 링크, 메타데이터 변경, 잘못된 컨테이너·마운트, 전송 파일 변조와 출력 주입을 검사한다.

배포 ZIP 자체에 대한 재검증 결과는 artifact의 VALIDATION.json에 기록한다. Linux PowerShell 7 검증은 실제 Windows PowerShell 5.1 실행 결과를 대신하지 않는다. 실제 Windows 백업·복원·오프라인 인계는 수행하지 않았다.

이 검사는 대상 스키마의 완전한 의존성 폐쇄, 소유권·권한·확장·뷰/함수 본문 의존성, 모든 별도 스키마나 DB를 보장하지 않는다. DB와 파일의 공동 원자적 스냅샷 및 파일 내용 동일성도 확인하지 않는다. writer 정지 증명이 없고 백업 파일을 만들지 않으므로 성공 후에도 `backup_ready=false`, `installation_ready=false`다. 외래 키/상속 외의 교차 스키마 연결은 별도 검토가 필요하다.

기존에 사용자가 고른 별도 기기·오프라인 보관 방식을 유지한다. 실제 대상 경로나 최신 참조·해시 인계는 아직 완료되지 않았다. 이번 출력의 메타데이터 해시는 내용 해시·복원 참조·실제 백업이 아니다.

## Windows 실행

ZIP을 바탕화면에 풀고 [패키지 안내](../../../tools/windows_demo_backup_preflight/README_KO.md)의 CMD 명령을 실행한다. 키 세션 등록 없이 실행 가능하다. 성공 문구는 `BACKUP_SCOPE_OBSERVED_KEEP_PAUSED_NO_BACKUP_NO_ORDERS`이며 차단 시 자동 재시도하지 않고 결과를 검토한다. 결과가 도착하면 실제 보존 범위를 확정하고 백업·복원 연습 설계로 진행할 수 있다.
