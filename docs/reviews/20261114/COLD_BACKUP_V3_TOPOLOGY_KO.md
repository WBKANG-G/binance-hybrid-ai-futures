# 콜드 백업 v3: 컨테이너 정보 프레임과 필드별 진단

사용자의 v2 RUN_CHECK 화면은 내부 계획이 `CONTAINER_TOPOLOGY / BACKUP_PLAN_RUNNER_FAILED`에서 차단됐다고 보고했다. PG probe는 모두 NOT_CHECKED이고 정지 시도·이미지 저장·백업 생성은 없었다. 실제 Windows 원문이나 예외를 원격 조회하지 않았으므로 이 화면만으로 구체적인 속성·파서 오류를 확정하지 않는다.

## 변경

기존 Docker --format 인수에는 `{"id":{{json .Id}},...}` 형태의 literal JSON 따옴표가 있었다. Legacy native 인수 전달에서는 이 따옴표가 사라져 엄격한 JSON으로 해석할 수 없는 출력을 만들 수 있다. Linux PowerShell 7은 이를 허용해 이전 시험에서 통과했지만, 그 결과를 Windows PowerShell 5.1 파서 호환성으로 간주할 수 없다.

v3는 따옴표를 직접 조립하지 않고 `{{json ...}}` 필드를 `{{println}}`으로 연결한다. Docker가 JSON quoting을 생성하며 컨테이너당 11행·총 64KiB, 볼륨당 3행 계약을 확인한다. 각 행은 PowerShell 내부에서 `{"value":<행>}`로 감싸 변환한다. 이 내부 문자열은 native argv로 전달하지 않는다. 원본 배열·단일 항목 배열·빈 배열·null을 유지하고 속성의 존재·자료형을 명시적으로 검사한다.

source ID, 정책·볼륨·외부 소비자·PG 버전·pause/lease·개수·정지 조건은 완화하지 않는다. 전체 Config.Env는 읽지 않는다. 새 코드는 원본 JSON/label/호스트 경로를 공개하지 않는다.

`topology_probe`에는 허용된 작업, 필드 enum, 형식이 확인된 컨테이너 ID만 기록한다. JSON 변환 실패, 필드 수 불일치, 누락/잘못된 필드는 각각 BACKUP_PLAN_TOPOLOGY_JSON_INVALID / FIELD_COUNT / FIELD_INVALID로 구분한다. 기존 안전한 진단 투영을 통해 `fresh_plan` 아래 전달한다.

## 검증과 운용

Standard/Legacy 모드에서 정상·비공개 출력 주입·누락 restart/mount 속성·잘못된 배열·필드 수 불일치·개별 JSON 손상을 확인한다. 실제 Docker에서 새 프레임을 Python의 strict JSON 파서로 확인하고, 이전 literal-key JSON이 Legacy에서 strict JSON이 아닌 출력을 만드는 것도 재현한다. 이는 실제 Windows 원인을 확정하는 시험은 아니다.

실행기의 RUN_CHECK 무중단·무백업 동작과 기존 실제 중단·이미지 저장·로컬 export·새 볼륨 복원을 회귀 검사한다. Windows NTFS ACL은 Linux 합성 시험에서 대체하며 production ZIP에는 우회 코드가 없다. 실제 Windows 결과는 아직 확인 전이다.

우선 RUN_CHECK.cmd만 실행하고 콘솔 JSON과 종료 문구를 공유한다. RUN_BACKUP.cmd는 여전히 원본 API/DB를 정지하는 실제 백업 명령이므로 진단 단계에서 실행하지 않는다. 실제 데이터 tar/manifest/상태 파일은 공유하지 않는다.
