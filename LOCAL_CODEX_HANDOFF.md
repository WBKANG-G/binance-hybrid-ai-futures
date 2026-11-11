# Windows 로컬 Codex 작업 인계 — 2026-11-11 KST

이 파일은 웹 Codex와 별개인 Windows 로컬 Codex 세션을 위한 인계입니다. 로컬 터미널/Docker 접근이 실제로 가능한지 확인한 뒤 작업하세요. 웹 세션은 Linux /workspace에 연결되어 있었으며 Windows 명령을 직접 실행했다고 주장하지 않았습니다. 사용자에게는 한국어로 설명하세요.

## 사용자 목표와 승인

사용자는 단계가 끝나면 스스로 결과를 검토하고 다음 개발 단계로 진행하길 원합니다. 일상적인 읽기, 코드 수정, 격리 테스트, 필요한 수정 패키지 작성 및 해당 GitHub 저장소 게시를 진행하도록 승인했습니다. 같은 승인을 반복해서 묻지 마세요. 권한 승인은 외부 주문 사용량·거래소 비용·계정 활동에 대한 증거를 대신하지 않습니다. API 키나 Secret을 채팅으로 요구하거나 출력·파일 저장·Git 커밋하지 마세요.

## Windows에서 실제 확인된 완료 상태

사용자가 RUN_NEXT.cmd 실행 결과를 공유해 다음을 확인했습니다.

- 최종 문구: CODE_AND_STORAGE_CONFIGURED_KEEP_PAUSED_NO_ORDERS
- runtime_storage_setup: CONFIGURED_KEEP_PAUSED
- storage_bindings_verified: true, startup_ready: true, leader_released: true
- risk_state: NORMAL, paused: true
- day_kst: 2026-11-11, week_kst: 2026-11-09
- daily_base_usdt와 weekly_base_usdt: 4998.74183608
- high_water_usdt: 5000.00000000
- 준비·최종 등록·읽기 전용 사후 확인에서 preserved_ledger_sha256가 같았습니다. 해시 값은 로컬에서 다시 읽어 확인하고 스크린샷을 전사해 임의로 만들지 마세요.
- http_attempts: 0, order_writes: 0, credentials_accessed: false
- execution_approved, orders_enabled, live_trading_enabled는 모두 false

코드와 저장소 설치는 끝났습니다. 설치·기준금액 적용을 다시 하지 마세요. 구성 스크립트를 실험 용도로 반복하거나 SQLite/보조 DB를 초기화하지 마세요. 실제 계정으로 synthetic runtime을 열어 테스트하면 세션 이벤트가 생겨 초기 구성 검증 전제도 달라질 수 있습니다.

## 실행 환경과 자료

현재 사용자가 실행한 폴더는 대체로 다음 위치입니다.
%USERPROFILE%\Desktop\windows-demo-runtime-auto-setup-v2-20261111

실제 Docker 대상:
- hybrid-windows-api-1
- hybrid-windows-db-1
- hybrid-windows-key-session-1

web/alerts는 이전 화면에서 정지 상태였습니다. 먼저 현재 상태를 읽으세요. 이름에 backup/failed/candidate가 들어가는 컨테이너는 배포 복구용일 수 있습니다. 모든 컨테이너를 일괄 시작하지 말고, 백업 컨테이너·이미지·볼륨을 삭제하지 마세요.

원래 Compose 작업 폴더와 파일은 실제로 찾을 수 없었습니다. 최신 설치기는 현재 컨테이너의 Config/HostConfig와 네트워크 정보를 보존해서 기존 Docker 연결을 통해 메모리에서만 새 컨테이너 생성 요청을 전송합니다. 정상 교체 시 이전 API 컨테이너를 중지 상태로 보관합니다. 환경변수를 호스트 파일/로그/명령 인수로 내보내지 않습니다. 원래 Compose 경로가 없다고 이것을 새 고장으로 되돌리지 마세요.

배포 정보는 code-install/INSTALL_STATE.json에 있습니다. 이 파일은 환경변수가 아닌 이미지·컨테이너 식별자를 보관합니다. 현재 이미지와 이 기록을 먼저 확인하세요. 전체 docker inspect 결과는 환경변수를 포함하므로 파일·로그·채팅에 덤프하지 말고 필요한 안전한 필드만 출력하세요.

영속 저장소는 기존 /app/demo-export-state 볼륨의 다음 하위 경로입니다.
- demo-runtime-20261111/order-quota
- demo-runtime-20261111/shared-weight

설정 operation 키:
- MANAGED_QUOTA:STORAGE_V1
- MANAGED_RUNTIME:SHARED_STORAGE_V1
- MANAGED_RUNTIME:STORAGE_SETUP_20261111_V1
- MANAGED_RUNTIME:STORAGE_SETUP_COMPLETE_20261111_V1

추가된 보조 PostgreSQL 테이블:
- demo_shared_weight_installation
- demo_shared_weight_bindings
- demo_shared_weight_checkpoints

설정 영수증을 통해 기존 금융 원장·기준금액·고점·평가 시각·과거 영수증을 보존했습니다. 이전 일간 적용 영수증의 전체 원장 해시는 뒤에 정상적인 저장소 등록 operation이 추가되면 바로 검증할 수 없으므로, 구성 도구의 setup_context/setup_preserved처럼 허용된 구성 operation만 사본에서 제외해 검증합니다. 실제 원장이나 기존 영수증을 고쳐서 검증을 통과시키지 마세요.

## 소스 확보

저장소: https://github.com/WBKANG-G/binance-hybrid-ai-futures
현재 개발 기준 커밋: e03b6ba683732cd5872c30cf0d245a5b1fb6d325
해당 소스 브랜치: windows-demo-tp-observation-lease-20261111

호스트에 온전한 저장소가 없으면 배포 폴더를 덮어쓰지 않는 별도 개발 폴더에 위 브랜치를 가져오세요. 먼저 실제 설치 소스와 비교하세요. 저장소 AGENTS.md와 MASTER_SPEC_V1_0.md를 읽고 잠긴 요구사항을 지키세요. 설치된 제어 코드 66개 해시 및 기존 핵심 코호트 55개 해시는 배포 폴더의 code-install/PAYLOAD_REFERENCE.json과 helper에 있습니다. 배포 이후 소스를 바꾸면 기존 코호트 검증이 달라지므로 새 검증/배포 계획을 함께 준비하세요.

게시된 최종 설치 패키지:
https://raw.githubusercontent.com/WBKANG-G/binance-hybrid-ai-futures/a342416253192db4bf9106051d1e8d9fdf022948/windows-demo-runtime-auto-setup-v2-20261111.zip
ZIP SHA256: 9983fc3fcb9e7ecb529a260422cc9a488322e0b872ddae833c314d41d3ee70a3

## 남은 개발과 먼저 할 일

1. 읽기 전용으로 현재 Docker/API/DB와 저장소 완료 영수증을 확인하세요. 이미 완료된 설치를 다시 실행하지 마세요. 바이낸스 요청이나 키 등록이 필요 없는 로컬 확인부터 하세요.
2. 실행 차단 조건을 실제 코드와 증거를 기준으로 정리하고 해결 가능한 부분을 구현·테스트하세요. services/execution/demo_managed_runtime.py의 assemble_managed_runtime는 기본적으로 RUNTIME_REAL_EXECUTION_DISABLED를 거부하고 synthetic 전용입니다. 저장소 설치를 실제 TP 실행 완료로 보고하지 마세요.
3. 계정 주문 사용량은 UNKNOWN입니다. services/execution/demo_order_admission.py는 count/주문 카운터 헤더가 없으면 ORDER_COUNTER_NOT_PROVIDED로 차단합니다. 공식 rateLimit/order 응답이 한도만 보장하는 문제를 이미 확인했습니다. 같은 limits-only 조회를 반복해 성과로 보고하거나 누락 카운터를 0으로 처리하지 마세요.
4. 공유 예산은 협력 프로세스의 로컬 압력을 추적합니다. 실제 외부 IP 전체 용량을 예약했다고 주장하지 마세요. SDK 문서에 고정한 비용 정책과 실제 거래소 비용 상한 증명도 구분하세요.
5. 실제 실행 경로를 설계하더라도 기본 차단·검증 조건을 우회해 플래그만 켜지 마세요. 우선 코드 개발과 격리 테스트를 진행하세요. 실거래 활성화·실자금 주문은 금지하고, 현재 차단 상태에서 DEMO 주문도 보내지 마세요.
6. 추가 계정 활동이 없다는 운영자 확인은 2026-11-11까지의 답변입니다. 이후 날짜나 다른 계정에 자동 연장하지 마세요. 새 날짜가 됐다고 이 인계의 기준값을 강제로 다시 적용하지 마세요.

현재 유효한 차단 상태:
- external_account_order_usage: UNKNOWN
- actual_ip_capacity_reserved: false
- venue_request_cost_upper_bounds_proven: false
- 실제 실행 기본 차단 유지

DEMO 세션은 최대 12시간의 RAM 저장소를 사용합니다. Docker 재시작으로 없어지거나 만료될 수 있습니다. --status 같은 안전한 상태 조회를 사용하고, 기존 키를 노출하지 마세요. 새 키 입력이 정말 필요하면 사용자 로컬의 숨김 입력 경로를 사용해야 하며, 새 에이전트가 키를 이미 안다고 가정하지 마세요.

## 검증과 보고

- 저장소 구성은 격리 PG/SQLite 및 Docker 29개 + PowerShell 자동 흐름 12개가 통과했습니다. 독립 Docker 프로세스/영속 볼륨/UID 10001로 약 70초의 실제 시작 대기를 확인했습니다.
- Compose 복구 수정은 설정 보존/전달/모의 설치 29개 + 실제 Docker/PG 정상 설치 1개 + 감사 이력 변경 시 원래 API 복원 1개, 총 31개가 통과했습니다.
- 기존 합성 런타임 소스의 과거 회귀 1,872개 통과는 과거 기록이며, 이번 작업에서 새로 실행한 검사 수로 재사용하지 마세요.
- Cloud PowerShell 검사는 Linux의 PowerShell 7.4.6에서 수행했습니다. Windows 5.1의 배열 반환 형태를 모의했고 실제 PC에서 최종 설치 성공을 사용자 화면으로 확인했습니다.

사용자 Docker 환경에는 실제 계정 상태가 있습니다. 테스트는 별도 컨텍스트/격리 DB 스키마/고유 테스트 컨테이너 이름으로 수행하세요. 특히 배포 복구 통합 검사 중 hybrid-windows-api-1 이름을 사용하는 Cloud 테스트를 사용자 Docker에 그대로 실행하지 마세요.

실행 결과를 검토하고 의미 있는 다음 작업으로 자율 진행하세요. 완료하지 않은 Windows 실행·실제 거래소 검증·거래 수익성을 완료로 표현하지 마세요. 중요한 실패는 원인을 재현하고 필요한 검사만 다시 실행하세요. 단계마다 새 ZIP을 요구하는 흐름을 반복하는 대신 로컬에서 수정·검증을 이어가세요.
