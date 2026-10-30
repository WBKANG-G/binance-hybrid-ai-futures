# DEMO 키를 한 번 입력하고 같은 컨테이너에서 재사용하기

10월30일 적용 결과 OPERATOR_ATTESTED_PERIOD_BASELINES_ADOPTED_KEEP_LOCK이 확인됐습니다. 작업ID는0c2b089c-36d5-4561-8630-572e952e0933이며 일별/주별 기준은 이미 적용됐습니다. 다시 적용하지 않습니다. EMERGENCY와 paused는 유지 중입니다. 이제 키를 한 번 등록하고 복구 검토를 실행합니다.

## 1. 새 폴더 설치

기존 Galaxybook PC에서 ZIP을 새 windows-demo-key-session 폴더에 풀고 열어 탐색기 주소창에 cmd를 입력합니다. CMD 경로가 새 폴더인지 확인합니다.

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --wait
```

기존 hybrid-windows 프로젝트와 PostgreSQL/demo_export_data 볼륨을 유지합니다. 이 버전은 API 컨테이너에 전용 tmpfs 메모리 영역만 추가합니다. 키를 이미지·DB·.env·호스트 공유 폴더에 기록하지 않습니다. 이전의 기간 기준/최초 체결/worker 대기/조회/시각 수정도 포함합니다.

## 2. 이번 컨테이너에서 키 한 번 등록

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -it api python -m scripts.demo_key_session --register
```

DEMO API Key와 Secret을 각각 숨김 입력합니다. 정상 결과는 REGISTERED_IN_PRIVATE_TMPFS입니다. 이 명령은 거래소나 DB를 호출하지 않고, 주문·잠금 해제·기준 변경을 하지 않습니다. 키를 채팅이나 명령 문자열에 넣지 않습니다. 기존 등록이 있으면 자동 교체하지 않으므로 아래 --clear로 먼저 지우고 다시 등록합니다.

## 3. 키 재입력 없이 복구 검토

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.review_demo_recovery
```

이제 등록된 키를 읽으므로 다시 입력할 필요가 없습니다. 기본 복구 검토는 거래소 조회와 DB 읽기만 하며 주문/DB 변경/잠금 해제를 하지 않습니다. 결과 화면만 공유합니다. 새 검토 토큰 확인 후 별도의 복구 승인과 새 DEMO 검증을 진행합니다. 키 등록 자체는 복구 승인이 아닙니다.

## 상태 확인/삭제

등록 상태 확인(키 값은 출력하지 않음):

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.demo_key_session --status
```

메모리에서 등록 파일 삭제:

```cmd
docker compose -p hybrid-windows -f infra/docker-compose.yml exec -T api python -m scripts.demo_key_session --clear
```

삭제는 이후 새 명령의 자동 키 읽기를 막습니다. 이미 실행 중인 작업이 읽은 키를 회수하거나 주문을 취소하는 명령은 아니며, 매매 중지는 별도 절차입니다.

## 사용 기간과 보관 범위

자동 사용 유효기간은 등록부터12시간입니다. 만료/시계 역행/잘못된 권한/손상된 파일은 다른 키로 임의 전환하거나 자동 재입력하지 않고 차단합니다. 만료되면 --clear 후 --register로 다시 입력합니다. 12시간은 자동 사용 유효기간이며, 사용하지 않는 파일을 정확히 만료 초에 자동 삭제하는 타이머는 아닙니다. 등록 파일은 삭제하거나 컨테이너를 중지·재시작·재생성하면 사라집니다. 이 경우 한 번 다시 입력해야 합니다. 매 명령마다 입력하는 과정만 줄이는 휘발성 방식입니다.

키는 Linux Docker tmpfs의 평문 메모리 파일이며 디렉터리0700/파일0600, 컨테이너 사용자10001 전용입니다. 일반 디스크 파일/영구 볼륨/이미지/환경 설정/DB/로그로 저장하지 않습니다. 같은 컨테이너 사용자 또는 관리자/root가 메모리에 접근할 수 있고, 호스트 메모리·swap·VM 스냅샷 보호는 호스트 설정에 따릅니다. 영구 Windows 자격 증명 보관 기능이나 재부팅 후 무인 로그인 기능을 추가한 것은 아닙니다.

복구·일별/주별/과거 기록 검토 CLI와 run_demo가 등록 키를 읽습니다. 예전의 긴 python -c 명령에 직접 적힌 getpass 호출은 계속 입력을 요청하므로, 이후 안내는 해당 CLI를 직접 실행하는 짧은 명령을 사용합니다. 등록이 없을 때 기존 조회 CLI의 숨김 입력도 유지됩니다. run_demo는 등록 또는 기존 명시적 런타임 키가 필요하며 자동 숨김 입력은 하지 않습니다. 명시적인 DEMO 런타임 키 두 값이 있으면 그것을 우선하고, 하나만 있거나 일반/LIVE 키와 섞이면 등록 키로 우회하지 않고 차단합니다.

LIVE/Phase5, 주문 수량·진입·청산 규칙, 위험 검사, 기준/잠금, export 생성 경로는 바꾸지 않았습니다. 설치와 키 등록은 자동 매매를 시작하지 않습니다.
