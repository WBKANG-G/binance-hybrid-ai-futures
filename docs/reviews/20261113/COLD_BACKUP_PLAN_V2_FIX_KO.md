# Windows 콜드 백업 계획 v2: 구형 인수 전달 호환성 수정

사용자가 실행한 v1은 `POSTGRES_CAPABILITY / BACKUP_PLAN_POSTGRES_LAYOUT_UNSUPPORTED`에서 차단됐다. 화면상 서비스 정지·재시작 정책 변경·DB/파일 변경·백업 생성·거래소 요청·주문 쓰기는 모두 없었다. 화면만으로 실제 PGDATA·식별자·도구 버전의 원문은 알 수 없다.

## 재현한 결함

v1은 PGDATA·POSTGRES_USER·POSTGRES_DB를 한 `sh -c`의 따옴표 포함 printf 명령으로 읽었다. PowerShell 7 기본 native argv 테스트만 했으며 PowerShell 5.1의 구형 전달 방식 검증을 빠뜨렸다. 이는 배포 검증의 누락이다.

Linux PowerShell 7의 `Legacy` 인수 전달과 실제 Docker/PG18 도구로 재현한 결과, shell에 도착하는 따옴표가 달라져 printf의 `\n`이 문자 `n`으로 해석됐다. 세 필드가 하나의 줄로 붙어 v1의 다섯 줄 계약을 충족하지 못한다. `Standard` 전달에서는 세 필드가 정상 분리됐다. 이는 사용자의 차단과 일치하는 재현 사례이며, 실제 Windows 내부 필드를 원격 확인했다는 뜻은 아니다.

## 변경 범위

v2는 아래 고정 명령을 별도 argv로 실행한다.

- `printenv PGDATA`
- `printenv POSTGRES_USER`
- `printenv POSTGRES_DB`
- `postgres --version`
- `pg_controldata --version`

셸·따옴표·줄바꿈 형식 문자열에 의존하지 않는다. 각 결과는 정확히 한 줄이어야 한다. 기존의 지원 경로·식별자 형식·PG 15–18·server/control major 일치·볼륨/외부 소비자 검사 조건은 유지한다.

차단 시 `postgres_probe`에는 NOT_CHECKED, READ_FAILED, UNSUPPORTED_LAYOUT, INVALID_FORMAT, UNSUPPORTED_VERSION, VERSION_MISMATCH_OR_INVALID 등 고정 상태 코드만 표시한다. 원본 경로·사용자·DB명·예외·비밀번호는 표시하지 않는다. 기존과 동일하게 관측 전용이고 백업·복원 mutation 코드는 ZIP에 포함하지 않는다.

## 검증

두 native 전달 모드에서 정상·잘못된 경로/식별자·누락/중복 출력·지원 외 버전·도구 major 불일치·볼륨/컨테이너 변경·변조·비공개 출력 차단을 검증한다. 별도로 UUID로 만든 새 sleeping 컨테이너와 볼륨에서 **실제 Docker inspect/printenv/PG 도구**를 실행하고, v1 결함 재현 및 v2 통과를 두 모드에서 확인한다. 해당 컨테이너는 DB나 앱을 초기화하지 않고 검사 후 직접 만든 자원만 정리한다.

테스트 launcher는 자식 PLAN.ps1의 `$LASTEXITCODE`를 외부 프로세스로 전달한다. 배포 CMD는 v1과 동일하게 PLAN.ps1을 직접 `-File` 실행한다.

최종 시험 수와 소스·ZIP 해시는 v2 artifact의 VALIDATION.json에 기록한다. Linux PowerShell 7 Legacy 검증은 실제 Windows PowerShell 5.1 실행 검증을 대신하지 않는다. 새 ZIP을 실제 Windows에서 실행한 결과를 추가로 확인해야 한다.
