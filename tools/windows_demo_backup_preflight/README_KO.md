# Windows DEMO 백업 범위 사전 검사

이 패키지는 **백업 범위 관측 전용**입니다. 백업 생성·복원·설치·writer 정지·거래소 요청·시간 복구·키 세션 등록을 하지 않습니다. 기존 API 컨테이너에 Python 코드를 stdin으로 전달하며 코드를 복사하거나 앱 모듈을 import하지 않습니다. 출력은 콘솔에만 표시합니다.

ZIP을 바탕화면에 풀고 Windows 명령 프롬프트에서 실행하세요.

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-backup-scope-preflight-20261113"
RUN_PREFLIGHT.cmd
```

키 세션을 준비할 필요가 없습니다. 실행 결과 전체를 공유해 주세요. 성공 종료 문구는 `BACKUP_SCOPE_OBSERVED_KEEP_PAUSED_NO_BACKUP_NO_ORDERS`입니다. 차단되면 원인 코드를 공유하고 자동 반복 실행하지 마세요.

## 관측 범위

- 컨테이너 ID·이미지와 `/app/demo-export-state` 마운트를 전후 대조합니다. 호스트 경로와 볼륨 이름은 출력하지 않고 마운트 식별 해시만 표시합니다. 저장 루트 아래 별도 중첩 마운트가 있으면 차단합니다.
- DB 연결 설정은 기존 컨테이너의 `DATABASE_URL`을 메모리에서만 사용합니다. READ ONLY / REPEATABLE READ 트랜잭션으로 현재 스키마의 테이블·시퀀스·뷰·관련 객체 수와 크기를 관측합니다. 접두사 필터를 쓰지 않으며 V2 테이블도 존재하면 포함합니다. 알려진 테이블 이름만 출력하고 다른 이름은 `UNCLASSIFIED`와 식별 해시로 표시합니다.
- DB 함수·트리거·인덱스·제약 수, 다른 스키마와의 외래 키·상속 연결 수를 관측합니다. 계정의 일시정지 여부와 활성 lease 존재 여부만 읽으며, 이벤트 본문·주문 ID·자금·자격 증명은 출력하지 않습니다.
- 영속 루트 전체를 파일 내용 없이 메타데이터로 순회합니다. shared-weight / order-quota / OTHER 파일 수·논리 크기와 WAL·SHM·journal 파일 수를 표시합니다. 개별 파일명과 본문은 출력하지 않습니다. `metadata_sha256`은 경로·inode·시각 등의 관측 해시이며 내용 해시나 복구 참조가 아닙니다.
- 심볼릭 링크·파일 하드 링크·특수 파일·관측 중 메타데이터 변경은 차단합니다. 최대 DB 객체 128개, 파일·디렉터리 합계 1024개, 깊이 12, worker 전체 검사 예산 15초 및 SQL별 2초를 둡니다. DB 연결은 3초 제한입니다. 이는 Docker CLI 자체의 전체 실행 시간 제한은 아닙니다. 한도를 넘으면 더 큰 한도를 임의 적용하지 마세요.

## 결과 해석과 한계

성공은 관측 완료를 뜻합니다. 모든 결과에서 `backup_ready`, `installation_ready`, `writer_quiescence_verified`, `backup_created`, `restore_tested`, `execution_approved`, `orders_enabled`, `live_trading_enabled`는 false입니다. `account_paused=true` 또는 활성 lease 없음도 모든 writer가 정지했다는 증거는 아닙니다.

DB와 파일은 원자적 공동 스냅샷이 아니고 파일 본문은 검증하지 않습니다. 파일 내용이 메타데이터 확인 사이에 변경된 모든 경우를 검출하지 못합니다. 폴더 열람에 따른 OS 접근 시각 갱신은 발생할 수 있습니다. 크기는 관측 시점의 논리 크기 및 PG relation 크기로, 백업 압축 크기나 필요한 여유 공간의 보장이 아닙니다.

현재 스키마만 조사하므로 다른 스키마 전체·공유 객체·확장·권한·함수/뷰 본문으로 연결되는 모든 의존성을 검증하지 않습니다. 객체 수는 권한/정의/트리거의 정상성을 입증하지 않습니다. `UNCLASSIFIED`, cross-schema 연결, foreign table, unlogged relation, WAL/SHM 등은 후속 백업 설계에서 검토할 항목입니다. 기존 보관 참조나 실제 오프라인 인계도 생성하지 않습니다.

Windows PowerShell 5.1 문법으로 작성했습니다. 배포 검증에는 Linux PowerShell 7과 격리 Docker/PG를 사용하며 실제 Windows 실행 결과는 사용자가 실행한 후 확인해야 합니다.
