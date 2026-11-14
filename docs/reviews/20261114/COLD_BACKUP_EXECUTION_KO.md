# Windows 실제 콜드 백업 실행기 — 2026-11-14 KST

이 단계는 사용자에게서 확인한 v2 계획을 기준으로 **실제 정지·로컬 백업 명령을 준비한 단계**다. cloud의 합성 환경에서 실행·복원 시험을 수행했으며 실제 Windows 서비스나 데이터에는 접속하지 않았다. `RUN_BACKUP.cmd`를 사용자가 실행하면 원본 API와 DB가 정지되고 성공·실패 후에도 자동 재시작하지 않는다.

## 적용 범위와 재검사

고정 계정은 `DEMO-WINDOWS-ISOLATED-BTC-V1`이다. 검토된 원본 API와 DB의 컨테이너 ID를 고정하며, 총 대상 6개(API 3·DB 1·alerts 1·web 1), 실행 중인 API/DB 각 1개, runtime 소비자 3개, DB 볼륨 소비자 1개, 키 세션 1개 조건을 재확인한다. 대상의 재시작 정책은 모두 `no`여야 하며 자동 변경하지 않는다.

API/DB ID는 다음과 같다. 이는 사용자가 제공한 Windows 화면의 메타데이터이며 원격 조회 결과는 아니다.

- API: `4a8c061be784c3d73bd808a2da39d2374ed49f69294d1c64a2487db828e96026`
- DB: `42bb757768213d35c4322c15e68982aef690b572fc061615cbdc27649dadb041`

v2 계획 검사를 새로 수행하고 전역 컨테이너 집합·구성·볼륨을 대조한다. Windows 보호 폴더를 준비한 후 이미지를 먼저 저장한다. 직전의 계정 상태는 READ ONLY SQL로 paused=true·active lease=false·주문 34개·미해결 4개를 확인한다. 그 사이 구성이 바뀌면 정지 전에 거부한다. 원본 기간 키가 11월 13일이어도 14일로 자동 채택하지 않는다.

## 저장과 중단

- 새 `%LOCALAPPDATA%\WBKANG-DEMO-Backups\cold-20261114-<UUID>`에 NTFS ACL을 적용한다. 현재 사용자와 SYSTEM만 허용하며 소유자·규칙·reparse point를 검사한다. 폴더 날짜는 패키지 버전이며 금융 기간 변경을 뜻하지 않는다.
- 원본 API/PG 이미지를 canonical image ID로 `images.tar`에 저장한다. source 이미지 pull, 컨테이너 runtime 환경변수 전체, 키 세션 볼륨의 복사는 하지 않는다. 이미지만으로 컨테이너 writable layer의 임의 수정까지 보관한다고 주장하지 않는다.
- API에는 SIGTERM, PG에는 SIGINT를 보내고 정지를 관측한다. SIGKILL을 보내지 않으며 각각 30/60초 경과 시 차단한다. PG exit code 0, OOM 아님, `pg_controldata`의 `shut down` 관측을 요구한다.
- source 볼륨을 읽기 전용으로만 연결한 `network none` helper가 PG 데이터 볼륨과 runtime 볼륨 전체를 tar로 만든다. helper는 원본 이미지와 고정 entrypoint만 사용하고, 키 tmpfs를 마운트하지 않는다. PG_VERSION·postmaster PID 부재·디스크 여유·크기/항목/시간 제한을 검사한다.
- archive 후보는 심볼릭 링크·특수 파일·파일 hard link와 ACL/xattr 등 보존하지 못하는 확장 메타데이터를 거부한다. 실패한 결과를 소급 정상화하거나 일부 이력을 생략하지 않는다.

원본의 정상 종료 과정에서는 PG WAL/체크포인트 쓰기가 발생한다. 실행기가 주문·요청 상태·기간·잔고 기준을 바꾸는 SQL을 수행하지는 않는다. 키 세션은 실행 상태를 유지해야 하며 정지나 내용 복사를 하지 않는다.

## 로컬 검증과 실패 보존

Docker cp로 binary 파일을 내보내고 `Flush(true)`, 파일 ACL·소유자·길이·SHA256을 검사한다. manifest·이미지·상태 파일 해시를 영수증에 연결하고, source 컨테이너 집합·재시작 정책·정지 상태도 다시 확인한다. 정지 시 Docker가 비우는 IP/MAC/endpoint ID는 네트워크 소속과 구분해 비교한다.

보호 폴더에는 두 tar, manifest, 이미지 tar, 최소 상태 메타데이터, 중단 시도 기록, 성공 영수증 또는 가능한 범위의 실패 기록을 보관한다. 다른 프로젝트의 원문 메타데이터와 키 세션 마운트 정보는 상태 파일에서 제외한다. 실제 tar/manifest/상태 파일/영수증은 GitHub·채팅에 업로드하지 않는다.

실패 시 원본·helper·출력 볼륨·부분 파일을 자동 삭제·복원·재시작하지 않는다. 정지 신호가 전달된 뒤에는 `source_state_may_have_changed=true`로 표시한다. 강제 재시도 없이 콘솔의 고정 코드·단계·보관 위치/ID를 검토해야 한다.

## 검증의 범위

검증에는 소스 hash 고정/변조 거부, 명시적 실행 opt-in, Linux에서 Windows ACL 실행 거부, 권한·정책·계정·lease·개수·원본 구성 변화, 정상 종료 실패, PG control 상태, worker 출력 주입, 지원 외 확장 메타데이터와 로컬 복사 변조를 포함한다. 실제 PostgreSQL READ ONLY 트랜잭션에 쓰기를 주입해 거부와 원본 불변을 확인한다.

합성 Docker 시험은 새 internal 네트워크, API/PG 및 검토 대상 6개와 키 세션 컨테이너를 만든다. 실제 실행기와 Legacy native 인수 전달을 거쳐 API/DB 종료·이미지 저장·볼륨 복사·로컬 hash 검증을 수행한다. 내보낸 host tar를 새 볼륨으로 복원해 네트워크 없는 PG에서 계정 hash와 V2 테이블을 확인한다. 합성 계정의 저장된 `2026-11-13` 키가 보존되며 원본을 다시 시작하지 않는다. 시험에서 직접 만든 자원만 정리한다.

**Linux 시험에서 Windows ACL 함수는 시험용 복사본에서 대체했다.** NTFS DACL API의 실제 Windows 성공은 아직 검증하지 않았다. 배포 코드에는 해당 대체 함수나 권한 우회 옵션이 없으며 Windows 실행 시 실제 권한 검증을 한다. 성공한 합성 복원은 실제 Windows 백업의 복원 완료 증명이 아니다.

원본 container writable layer, 모든 runtime 환경변수·네트워크/호스트 설정을 포함하는 완전한 재해 복구 자동화가 아니다. 실제 백업의 격리 복원, 별도 기기 오프라인 인계와 최신 hash 보관, 설치·거래 준비는 완료로 표시하지 않는다. source quiescence는 Docker 관측 범위이며 특권 사용자의 동시 조작에 대한 독립 증명이 아니다.

배포 검증 수·소스 커밋·ZIP SHA256·합성 시험 결과는 artifact의 VALIDATION.json 및 DOCKER_EXECUTION_VALIDATION.json에 기록한다. 실제 Windows에서는 콘솔 JSON과 종료 문구만 공유하고 원본을 계속 정지 상태로 둔다.
