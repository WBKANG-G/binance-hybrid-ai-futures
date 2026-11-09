# accountConfig 조회 설치 패키지

현재 Windows 설치본70a854c에서 검증된 후보015fd20으로 API 코드만 갱신하기 위한 패키지입니다. main 변경/병합, 실제 주문, 거래 재개는 포함하지 않습니다. source commit과 검사 범위는 account-config-install-evidence/VALIDATION.json에 있습니다.

## 지금 실행할 단계: 사전 검사

ZIP을 바탕화면의 새 windows-demo-account-config-install 폴더로 풀고 RUN_PRECHECK.txt의 두 줄을 CMD에 입력합니다. 이 단계는 파일·DB·거래소를 읽기만 하며 빌드/설치하지 않습니다. 전체 전송 manifest 해시를 먼저 확인하고 모든 포함 파일을 확인한 뒤, 실행 중인 기존 API 컨테이너에서 stdin으로 가드를 실행합니다.

정상 결과는 PRECHECKED_PAUSED_FLAT_INSTALL_ONLY입니다. account_digest, exchange_digest, pair_signed_reads 세 값을 포함한 결과를 보관하세요. 이는 계정과 거래소의 설치 전 상태를 다음 단계에 묶는 값입니다. 키/서명/사적 응답 본문은 출력하지 않습니다.

설치 전 소스45개가 원본과 다르면 키/DB 접근 전에 중단합니다. 이후 NORMAL·paused·owner 없음·로컬 position/order 닫힘·원래 수동청산 receipt와 실패 trial을 확인합니다. 거래소 전체 position과 일반/algo 주문이 비었는지, 원래 entry와 stop/TP가 terminal인지 확인합니다. 전체 operational snapshot을2회 비교하며 금융·transfer·이력 값이 안정적이어야 합니다. 보호/주문/미확인 request 이력은 지우지 않습니다.

DEMO_CREDENTIAL_SESSION_UNAVAILABLE이면 키 세션이 필요합니다. 기존 명령으로 상태를 확인하고 만료된 임시 세션은 --clear 뒤 --ensure로 다시 등록합니다. 키는 숨김 입력에만 넣고 채팅에 보내지 마세요. 컨테이너가 정지됐다면 DB와 key-session의 상태/health를 먼저 확인해야 합니다.

## 날짜·주간 기준값

period_keys_match와 periods를 결과에 표시합니다. 이 코드 설치 검사는 날짜가 달라도 기준값을 재설정하지 않습니다. 기간 불일치는 거래 재개의 근거가 될 수 없으며, 정상 설치 이후에도 별도로 정산/기간 기준값과 readiness를 검증해야 합니다. 전날의 무활동 진술을 새 날짜까지 연장해서 가정하지 않습니다.

## 준비된 설치·사후 검사

사전 결과를 검토한 후 세 값을 넣어 같은 --phase pre 검사를 한 번 더 실행합니다. 이때 --expected-account, --expected-remote, --pre-reads를 함께 주면 계정/거래소 상태가 첫 검사 이후 바뀌지 않았는지 확인합니다. 통과 뒤 아래의 고정 Compose 명령으로 API만 재빌드합니다.

`docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --no-deps --wait api`

사후 가드는 `docker exec -i hybrid-windows-api-1 python - --phase post --expected-account <account_digest> --expected-remote <exchange_digest> --pre-reads <pair_signed_reads> < guard_account_config_install.py` 형태입니다. 꺾쇠 표시는 설명용 자리표시자이며 CMD에 그대로 붙여넣지 마세요. 실제 설치·사후 명령은 사전 결과의 세 값으로 완성해야 합니다. 모든 단계 사이에는 &&를 사용해 앞 단계 실패 시 이어서 실행하지 않도록 합니다. 설치 직전에도 manifest 전체 검증을 반복합니다.

사후 검사는 후보45개 소스 해시, 같은 계정 상태 해시, 같은 거래소 snapshot 해시, 서명 조회2회 감소를 요구합니다. 설치 전후 새로 읽은 값이 다르거나 global exposure가 있으면 중단합니다. 정상 결과는 POSTCHECKED_PAUSED_FLAT_INSTALL_ONLY이고 next_action은 KEEP_PAUSED_NO_TP_RETRY입니다. 기존 guard의 승인 해시는 바꾸지 않았습니다. 이미 새 소스로 바뀐 상태에서 pre를 다시 실행하면 원본 해시 검사에서 중단되므로, 첫 사전 결과를 보관해야 합니다.

사후 실패는 설치가 취소됐다는 뜻이 아닙니다. 일시정지 상태를 유지하고 원인을 확인해야 합니다. 자동 재시도·자동 rollback·실패한 TP 반복 실행은 없습니다. DB의 일자/주간/high-water, request/receipt/history, account owner/fence/lease를 이 가드가 수정하지 않습니다. health 확인과 원래 schema migration 외에 기존 DB나 key-session 볼륨을 재생성/삭제하지 않습니다.

## 검증 및 제한

설치 가드48개를 포함한 집중83개 통과. 생산 코드 두 파일은 이전에1612개를 통과한 후보와 바이트 동일하여 전체1612개를 이번 단계에서 다시 실행하지 않았습니다. 승인본의 버전 고정 검사52개도 이전 단계 결과입니다. 자연 lease SIGKILL5개 파일은 재실행하지 않았습니다. JUnit과 범위는 evidence에 있습니다.

기존 배포 ZIP과 fault-test 전용 의존성 tar, 오래된 SHA256SUMS는 API Dockerfile의 입력이 아니므로 이 ZIP에서 제외했습니다. API 빌드에 필요한 소스와 잠금 파일은 포함합니다.

고정 API Dockerfile과 frozen 의존성으로 클라우드 이미지를 빌드했고, network none/read-only root 컨테이너에서 후보·원본45개 소스 해시와 API import를 검증했습니다. 원본 컨테이너 검사는 같은 이미지에서 두 runtime 파일만 승인본으로 복원한 검사용 이미지이며 별도의 깨끗한 의존성 빌드는 아닙니다. 클라우드 DNS는 빌드 실행 옵션으로 해결했고 Windows Dockerfile/Compose는 변경하지 않았습니다. 클라우드에서 사용자 계정 API에 접속하거나 주문을 생성하지 않았습니다.

가드는75초 요청 시작 기한, 최대64 HTTP, 시계6회, 공개 분당 한도60%+추가 최대100 가중치 여유 조건을 사용합니다. 마지막 진행 중 HTTP는 timeout5초만큼 기한 뒤 종료될 수 있습니다. 418/429와 누락 가중치 헤더는 재시도 없이 중단합니다. 실제 활성 position/private-stream의 TP 요청 예산은 아직 검증되지 않았습니다.
