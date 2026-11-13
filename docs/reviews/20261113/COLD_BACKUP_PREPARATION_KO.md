# 콜드 백업 절차와 격리 복원 준비

이번 단계는 **합성 볼륨에서의 백업·복원 후보 검증과 실제 Windows 중단 범위 관측 도구 준비**까지다. 실제 Windows 컨테이너 정지, DB/파일 백업, 원본 복원, 별도 기기 인계, 설치는 실행하지 않았다. Windows ZIP에는 중단·백업·복원 코드를 넣지 않는다.

## 실제 관측의 근거와 남은 정보

사용자가 실행한 `0ae71bd04faa364115decebd17f535b0128d2631` 사전 검사의 화면은 완료 상태와 다음 값을 보고했다. 원격으로 다시 조회한 값이 아니다.

- DB relation 19개, 함수 2개, 트리거 5개, 인덱스 27개, 제약 144개. 외래 키·상속에 의한 다른 스키마 연결은 0개다. UNCLASSIFIED relation 1개가 있다.
- `/app/demo-export-state`는 writable named volume이며 중첩 Docker 마운트가 없었다. 파일 30개·디렉터리 15개, 논리 크기 309,255바이트다. shared-weight 10개/83,565바이트, order-quota 2개/20,480바이트, OTHER 18개/205,210바이트다.
- WAL·SHM·SQLite journal 파일 및 group/other writable 항목은 0개였다. 파일 본문, DDL 정의, DB/파일 원자적 일관성, 모든 writer의 정지는 검증하지 않았다. 계정 paused=true, active lease=false였으며 변경·거래소 요청·주문 쓰기는 없었다.

이 화면에는 PostgreSQL 데이터 볼륨, DB 이미지, 보관 컨테이너를 포함한 전체 볼륨 소비자와 재시작 정책이 없다. 그러므로 바로 서비스를 정지하는 대신, [Windows 계획 검사](../../../tools/windows_demo_backup_plan/README_KO.md)로 그 중단 범위를 관측한다. 이는 앞 단계에서 읽은 파일·테이블 수를 다시 수집하는 작업과 구별되는 Docker 토폴로지 검사다.

## 제안하는 실제 절차의 경계

1. 현재 계정의 일시정지와 전체 대상 컨테이너·이미지·볼륨·재시작 정책을 다시 고정한다. DB 클러스터의 다른 DB·역할·사용자에 미치는 중단 범위도 검토한다. 현재 Windows 계획 도구는 계정 상태를 다시 읽거나 DB 전체 목록을 조사하지 않으므로 이를 완료했다고 표시하지 않는다.
2. 백업은 별도 기기·오프라인 보관이라는 기존 선택을 따른다. 실제 인계 경로는 아직 미설정이다. 우선 `%LOCALAPPDATA%` 아래 별도 로컬 staging 위치를 만들고 Windows ACL을 현재 사용자와 필요한 시스템 권한으로 제한하는 실행기가 필요하다. 계정 상태·영수증뿐 아니라 PG 역할/인증/설정 등이 포함될 수 있으므로 실제 백업을 GitHub나 채팅에 업로드하지 않는다.
3. 백업에 필요한 원본 API·PG 이미지는 ID로 고정하고 오프라인 복구용 이미지 보관 방법도 준비한다. 이미지만 남아 있다고 가정하지 않는다. 구성·마운트·재시작 정책 복구 정보를 보호된 로컬 경로에 보관하되, 키 세션 tmpfs와 API 키·Secret·서명은 복사하지 않는다. 이번 후보의 두 tar 파일은 완전한 재해 복구 패키지 전체를 대신하지 않는다.
4. 검토된 writer와 보관 컨테이너의 자동 재시작을 차단하고 writer를 먼저, DB를 마지막에 정상 정지한다. 다른 source-volume 소비자 및 동시 Docker 조작을 재확인한다. Docker 외부의 특권 프로세스·호스트 DB 클라이언트까지 현재 도구가 독립 검증하는 것은 아니다.
5. DB의 정상 종료 코드와 `pg_controldata`의 `shut down` 상태를 확인한다. PGDATA 안의 postmaster PID, 외부 tablespace/pg_wal 링크, 별도 구성 마운트 등은 별도 거부·검증이 필요하다. 실제 Windows 정지·백업 실행기는 아직 구현하지 않았으며, 이번 archive 함수 자체도 정상 종료를 자동 판단하지 않는다.
6. **정지된 전체 PostgreSQL 데이터 볼륨과 전체 런타임 볼륨**을 함께 읽기 전용 마운트로 복사한다. 접두사·테이블 이름이나 기존 영수증 유형으로 이력을 필터링하지 않는다. 키 세션 볼륨은 helper에 마운트하지 않는다. 파일별 SHA256와 UID/GID·mode·mtime, 두 tar의 SHA256, 최종 manifest 해시를 생성하고 내구성 확인 후 별도로 보관한다.
7. 새로 만든 비어 있는 복제 볼륨으로만 복원한다. 원본 볼륨 또는 기존 대상에 덮어쓰지 않는다. 두 아카이브의 해시와 전체 헤더를 모두 확인한 후 추출하고, 복원된 전체 트리를 다시 스캔해 원본 manifest와 일치시킨다. manifest 해시는 백업 생성 성공 시 별도로 받은 값을 사용하며, 복원 순간의 파일에서 새로 계산해 신뢰하지 않는다.
8. 원본과 동일한 PG 이미지로 새 복원 DB만 `--network none`, TCP listen 비활성, read-only rootfs로 시작한다. 원본 계정의 주문/요청·이력·새 감사 테이블·함수·트리거·시퀀스의 동일성을 읽기 전용 SQL로 확인하고, 복제 런타임 SQLite도 읽기 전용으로 검증한다. 앱/주문 전송자는 시작하지 않는다.
9. 원본 서비스는 자동 재시작하지 않는다. 복원 실패 시 원본과 부분 백업·복제 볼륨을 보존하고 검토한다. 자원 환불·저널 재작성·요청 재실행·과거 이력 정리·거래 재개는 하지 않는다. 전체 DB와 파일을 함께 되돌린 상태의 최신성은 같은 PC의 해시만으로 독립 증명할 수 없다. 실제 오프라인 인계와 최신 해시 보관은 별도 완료해야 한다.

## 구현한 archive 후보

`tools/cold_backup_candidate.py`는 네트워크·앱 import·컨테이너 제어가 없는 POSIX 파일 복사 후보다. 기본 비활성이며 생성에는 `enabled=True`와 호출자의 동결 확인이 모두 필요하다. **호출자가 True를 전달하는 것은 독립적인 writer 정지 증명이 아니다.** manifest에도 source-freeze 독립 검증과 external custody를 false로 표시한다.

- 링크·특수 파일·파일 hard link·원본/출력/복원 경로 중첩·비어 있지 않은 대상·경로 탈출·중복 멤버·아카이브/파일/manifest 변조를 거부한다. regular file의 setuid/setgid는 허용하지 않는다. PG 볼륨에 필요한 디렉터리 sticky/setgid 메타데이터는 보존하되 실제 복원 후 동일성을 확인한다.
- descriptor와 NOFOLLOW로 원본을 읽고 파일 전후 stat을 확인한다. 원본 freeze를 대신하는 완전한 원자적 스냅샷이나 특권 사용자의 동시 조작 탐지 기능은 아니다.
- 그룹별 파일·디렉터리 50,000개, 파일 하나 2GiB, 논리 데이터 8GiB, 깊이 32, 생성/복원 예산 각 300초다. manifest는 16MiB 제한이다. 초과 시 자동 확장·부분 이력 생략·재시도하지 않는다.
- 생성 실패 시 부분 산출물을 보존하며, 성공한 호출이 반환한 retained manifest SHA256 없이 복원하지 않는다. fsync 실패 후 존재하는 파일만 보고 완료로 간주하면 안 된다.
- 격리 실험 helper는 root UID와 제한된 파일 읽기·소유권 복원 capability를 사용하고 source는 읽기 전용, clone/output만 writable로 마운트한다. 실제 Windows 백업용 privilege/ACL/orchestration 검증은 미완료다.

## 검증 결과의 의미

실험은 기존 사용자의 볼륨이나 cloud fixture DB를 쓰지 않고 UUID로 새 PG18·runtime·backup·clone·재스캔 볼륨을 만들었다. 두 원본 볼륨이 정지/읽기 전용인 상태에서 전체 파일을 복사하고, 새 볼륨으로 복원·재스캔했다. source와 clone의 manifest가 정확히 일치했다.

복원된 PG의 데이터·함수·트리거·시퀀스에 대한 논리 해시와 원본 해시가 일치했고, 주문 34개 형태를 확인했다. 복원 SQLite의 read-only quick_check와 행 수를 확인했다. 이 34개는 실제 계정 데이터가 아닌 **합성 fixture**다. 원본 PG는 계속 정지 상태였고 clone은 네트워크 없이 실행했다. 실험이 만든 자원만 종료·삭제했다. 실제 Windows 복원이나 실제 미해결 요청 4개의 해결을 주장하지 않는다.

최종 시험 수·ZIP 소스 해시·실험 보고서는 배포 artifact의 VALIDATION.json 및 DOCKER_RESTORE_DRILL.json에 기록한다. Windows PowerShell 5.1 문법을 사용하지만 자동 검증 호스트는 Linux PowerShell 7이며 실제 Windows 실행은 사용자의 결과로 확인해야 한다.
