# TP GET 연결 재사용 설치 — NORMAL 사전 검사 수정

이전 설치 검사는 NORMAL 상태에서 EMERGENCY 전용 복구 검토를 호출해 `RECOVERY_LOCK_STATE_MISMATCH`로 차단됐습니다. 설치 검사 구성 오류입니다. 기존 `&&` 실행에서는 사전 검사에서 멈춰 API 빌드는 시작되지 않았습니다. 이 패키지는 NORMAL 전용 읽기 검토로 수정합니다. 계좌를 EMERGENCY로 바꾸거나 기존 복구 검토의 잠금 조건을 완화하지 않습니다.

이번 패키지는 **코드 설치만** 합니다. 새 진입·TP 검증·강제 종료·복구·진입 재개를 실행하지 않습니다. 현재 NORMAL/진입 일시정지와 기존 이력을 유지합니다.

Windows 보안 설정은 유지하세요. CMD 실행 파일은 없습니다. 바탕화면의 새 폴더에 ZIP을 풀고 명령 프롬프트에서 실행합니다. 기존 폴더와 결과 파일은 보존하세요.

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-tp-read-pool-guard-fix"
```

다음 한 줄은 파일 검증 → 읽기 전용 사전 검사 → API만 갱신 → 읽기 전용 사후 검사입니다. 앞 단계가 실패하면 다음 단계로 넘어가지 않습니다.

```cmd
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $m=Get-Content -Raw 'TRANSFER_MANIFEST.json' | ConvertFrom-Json; foreach($p in $m.files.PSObject.Properties){if((Get-FileHash -Algorithm SHA256 -LiteralPath $p.Name).Hash.ToLower() -ne $p.Value){throw 'SOURCE_HASH_MISMATCH'}}; Write-Output 'TRANSFER_FILES_VERIFIED'" && docker exec -i hybrid-windows-api-1 python - --phase pre < guard_tp_read_pool_install.py && docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --no-deps --wait api && docker exec -i hybrid-windows-api-1 python - --phase post < guard_tp_read_pool_install.py
```

완료 화면을 보내주세요. 마지막 결과는 `tp_read_pool_install: POSTCHECKED_NORMAL_KEEP_PAUSED`, NORMAL/일시정지, 거래소 빈 상태여야 합니다. 차단/오류 시 재실행하거나 기존 복구를 다시 적용하지 말고 결과를 보존하세요.

사전·사후 검사는 최신 미체결 작업 `31c953e8-72dc-422d-89f3-8bbe7cdb5c0c`의 정확한 0.0007 BTC, CANCELED/CLOSED/체결0·잔량0·청산ID 없음, OPPOSITE_QUOTE 및 TP_TRIGGER:NOT_ACCEPTED 기록을 확인합니다. 활성 리더/로컬 잔여 노출이 없어야 하고, NORMAL 전용 읽기 검토에서 당일·주간 기준 일치, 기존 손실/낙폭 게이트, 3회 전체 스냅샷 안정성, 레버리지2·입출금 대조, 전체 과거 요청의 ID/종목/종료 상태와 기존 미해결 4건의 정확한 참조 집합을 확인합니다. 원래 예산(40초·서명 조회600·시각 조회6·과거 요청128·배치8)을 유지합니다. 조회 불확실성이나 집합 변경은 차단합니다. 복구 토큰을 생성하거나 미해결 상태를 지우지 않습니다. 각각의 검토 중 계좌/owner/fence/lease가 바뀌면 차단하며 토큰은 출력하지 않습니다.

## 변경과 유지되는 기준

새 옵션 `--enable-pooled-read-tp-observation`은 기존 명시적 OPPOSITE_QUOTE TP 범위에서만 사용할 수 있습니다. 이 설치는 옵션을 실행하지 않고 새 작업 ID도 만들지 않습니다. 옵션이 없으면 기존 경로를 유지하며 보호 worker와 다른 CLI/API/메일 worker는 바꾸지 않습니다.

기존 조회 전용 RecoveryTransport가 허용한 서명 GET·시각 GET만 연결을 재사용합니다. 공개 거래 규칙·호가·mark·캔들 GET과 모든 POST/PUT/DELETE는 기존 전송 함수에 같은 인자를 한 번 전달합니다. 재시도·응답 캐시·실패 시 다른 전송으로의 대체는 없습니다. 내보내기 생성 GET과 알 수 없는 GET/메서드는 차단합니다.

WSS 시각 검증과 실행 소비자의 여러 스레드가 같은 HTTPSConnection을 동시에 쓰지 못하게 직렬화합니다. 진행 중 GET이 끝난 뒤 연결을 닫고 원래 함수를 복원합니다. 새 옵션일 때만 안전한 풀 진단과 시도 수를 결과에 추가하며, 변이 요청 수에는 listen-key 요청도 포함됩니다. 실제 진입 수는 기존 전용 필드로 확인합니다.

LIMIT 진입 최대1, 0.0007 BTC/100 USDT 상한, fresh OPPOSITE_QUOTE 가격, 손절0.5%·목표1%, 신호 만료10초·관찰300초, WSS freshness, 위험/마진/스냅샷 대조, 2초 DB 잠금·60초 lease·fencing·정리 정책은 바꾸지 않았습니다. 핵심 소스12개의 동일 해시를 확인합니다. SDK·기존 풀·핵심 실행 서비스·마이그레이션·의존성은 수정하지 않습니다.

기존 Windows 측정은 SDK 155/152ms, GET 재사용 50/55ms라는 각 2개 표본이었습니다. 전체 제출 지연 개선이나 실제 체결을 보장하지 않습니다. 새 실제 주문 경로 옵션은 아직 실제 계좌에서 검증하지 않았으며 후속 준비 검사와 별도 단일 검증이 필요합니다. 이전 성공 SIGKILL·실패/미체결 기록을 바꾸거나 Phase 4 전체 완료를 선언하지 않습니다. LIVE는 비활성화 상태입니다.

기존 릴리스의 설치 검사 테스트는 실제 NORMAL 복구 경로를 연결하지 않아 이 오류를 놓쳤습니다. 이번에는 PostgreSQL의 실제 NORMAL 상태로 기존 오류 재현과 수정된 사전·사후 검사 통과를 함께 검증합니다. 앱 실행 경로(router/TP CLI)는 이전 GET-pool 릴리스와 동일한 바이트입니다. 설치 검사·테스트·문서만 수정했습니다. 현재 계좌에서의 설치 성공은 사용자 실행 결과로 확인해야 합니다.
