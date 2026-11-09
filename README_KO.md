# TP 예산 경로 설치 — 거래 재개 없이 코드만 갱신

소스 `5abc9f6c27f330136e318abf9f9bd4f958398763` / Windows 설치 기준 `4e967dc6751c6f04786ba63cdcc97e7eb1c80a39`

기존 `scripts.check_demo_tp_trigger` 명령을 새 WindowTPBudget 및 BudgetedTPTriggerCoordinator로 연결했습니다. 기존의 예산 없는 TP coordinator 선택을 제거했습니다. **실제 진입은 기본 거부하며 DB 임대·예약·작업 표식을 쓰기 전에 차단합니다.** 명령행이나 환경 변수로 모델 실행을 허용하는 옵션은 없습니다. 설치는 거래 재개 승인이 아닙니다.

## 설치 범위

Windows API 코드 갱신만 수행합니다. 새 DEMO 주문·TP 재시도·기간 기준값 채택·손실 락 해제·진입 재개는 없습니다. DB/키 RAM 볼륨, 수동청산 영수증, 이전 실패 및 미해결 요청, 일/주 기준값과 최고값을 보존합니다. LIVE 및 다음 Phase로 넘어가지 않습니다.

바탕화면에 압축을 풀어 폴더 이름을 `windows-demo-tp-budget-install`로 유지하세요. 기존 Docker/DB/API를 실행 중인 상태에서, GitHub의 이 패키지와 함께 게시한 **INSTALL_COMMAND.md**의 명령을 CMD에 복사합니다. 다운로드된 CMD/BAT/PS1/EXE 파일을 실행하지 않으며 Windows 보안 설정도 변경하지 않습니다.

명령은 다음 순서로 실패 즉시 중단합니다.

1. 고정된 manifest SHA256과 모든 파일 해시 검증
2. 45개 기존 설치 파일 해시 및 읽기 전용 사전 검사
3. API만 빌드·갱신하고 healthcheck 대기 (다른 서비스/볼륨 갱신 없음)
4. 55개 새 런타임 해시 및 사전 검사와 동일 계정/거래소 digest·조회 수를 요구하는 읽기 전용 사후 검사

성공 표시는 **`tp_budget_install: POSTCHECKED_BUDGET_CODE_ONLY_KEEP_PAUSED`** 입니다. `paused=true`, `remote_empty_verified=true`, `database_changed=false`, `order_writes=0`, `real_execution_default_disabled=true`여야 합니다. 결과를 보존해 공유하세요. 실패하면 이후 단계는 실행하지 않으며 자동 재시도·TP 시험·복구 적용을 하지 않습니다. 키 세션이 만료돼 사전 검사가 차단되면 빌드도 진행하지 않습니다. 실제 Windows에 접근한 cloud 검증은 없으므로 사용자 실행 결과로 설치 성공을 확인해야 합니다.

## 읽기 전용 설치 검사

계정의 owner/fence/lease와 원장 전체, 수동청산 영수증 및 -0.07032487 순손익, 과거 TP 실패 마커를 검증합니다. 모든 종목의 포지션·일반/Algo 주문이 비었고 두 스냅샷이 일치해야 합니다. 입출금·재무·설정·기간/손실 기준값은 사전/사후 동일해야 합니다. 날짜가 바뀐 경우 불일치를 보고하며 값을 고치거나 거래 준비 완료로 인정하지 않습니다. 계정 임대, DB 이벤트, POST/DELETE, listen-key 작업을 하지 않습니다.

사후 검사는 기본 진입 거부가 작동하는지 로컬 가짜 transport로도 확인합니다. 이 자기 검사는 실제 거래소 요청을 보내지 않습니다. 기존 설치 가드의 해시를 풀거나 새 코드에 맞게 완화하지 않았고 이번 버전 전용 가드를 사용합니다.

## 검증

**971개 선택 검증 통과**: 단위·핵심·설치 가드·CLI 회귀 970개와 canonical CLI를 통한 실제300초 모의 시험 1개입니다. 새 CLI는 모의 용도로만 테스트가 constructor를 명시적으로 바꿨고 모든 HTTP/체결은 FakeDemo, WSS는 localhost였습니다. 실제 운영 명령에는 그 옵션이 없습니다.

300초 시험은 진입1회, TP 체결 후 최종 VERIFIED_FLAT, 별도 시장가 청산0회였습니다. 모든 분 시작 시각 및 60.001초 경계 여유를 포함한 최대 모델 비용은 **4,227**, 한도6,000에서 코드 상한5,700 이내였습니다. 이전 예산 엔진의 4개 자연시간 및 추가2개 경로 검증을 변경하지 않았으며 런타임 coordinator/위험/DB/스냅샷 소스도 직전99ef514와 같습니다.

Docker에서는 기존 설치 기준 이미지의45개 pin과 새 소스 overlay 이미지의55개 pin, 기본 진입 거부 및 PAPER API readiness HTTP200을 확인했습니다. 새 의존성을 처음부터 받는 전체 빌드 검증은 아니며 기존 검증 이미지에 소스 레이어를 교체한 smoke입니다. 실제 거래소 비용 상한·공유 IP 용량·별도 주문 횟수 제한을 증명하지는 않았습니다. 따라서 설치 후에도 실제 진입은 거부합니다.

전체 소스(중복된 과거 ZIP/의존성 tar.gz 2개 제외), 새 가드와 검증 결과가 들어 있습니다. `validation/`은 가짜 계정의 기록입니다. 이 안의 sample digest를 Windows 사후 명령에 재사용하지 마세요. 설치 명령이 현재 Windows 사전 검사 값을 직접 전달합니다.
