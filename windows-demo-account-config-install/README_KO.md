# TP 조회 제한 완화·정리 전 로컬 정지 수정 — 설치만 수행

이번 패키지는 코드 설치와 읽기 전용 사전·사후 검사만 수행합니다. 신규 진입·TP 시험·복구·진입 재개는 실행하지 않습니다. 현재 수동 종료 작업 `041d1f35-870e-46cb-aad2-ed6059bc1095`의 CLOSED/잔여0, NORMAL/진입 일시정지와 수동 종료 영수증을 보존합니다.

ZIP을 바탕화면에 풀고 CMD에서 실행하세요. Windows 보안 설정을 유지합니다. 다운로드한 CMD/BAT/PS1 실행 파일은 없습니다.

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-tp-rate-mitigation"
```

아래 한 줄은 전체 파일 해시 검증 → 읽기 전용 사전 검사 → API만 빌드·갱신 → 읽기 전용 사후 검사입니다. 한 단계가 실패하면 이후 단계는 실행하지 않습니다. Docker와 창을 유지하고 결과를 보내주세요.

```cmd
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $m=Get-Content -Raw 'TRANSFER_MANIFEST.json' | ConvertFrom-Json; foreach($p in $m.files.PSObject.Properties){if((Get-FileHash -Algorithm SHA256 -LiteralPath $p.Name).Hash.ToLower() -ne $p.Value){throw 'SOURCE_HASH_MISMATCH'}}; Write-Output 'TRANSFER_FILES_VERIFIED'" && docker exec -i hybrid-windows-api-1 python - --phase pre < guard_tp_rate_install.py && docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --no-deps --wait api && docker exec -i hybrid-windows-api-1 python - --phase post < guard_tp_rate_install.py
```

성공 결과는 `tp_rate_install: POSTCHECKED_KEEP_PAUSED`입니다. NORMAL/paused=true, remote_empty_verified=true, database_changed=false, order_writes=0이어야 합니다. 오류 시 재실행·기존 복구 적용·신규 시험을 하지 말고 결과를 보존하세요. 이미지는 갱신하지만 기존 DB/수동 종료 영수증/키 RAM 볼륨과 다른 서비스는 유지합니다.

## 수정 내용

- 풀 연결 TP 옵션의 유휴 대조 간격을 기존 5초에서 `min(15초, 기존 max_snapshot_age_seconds/2)`로 조정합니다. 현재 정책의 freshness 한도30초에서는 15초입니다. 이벤트 수신은 대기를 즉시 깨우며, 미체결 진입의 만료가 더 가까우면 해당 만료에 맞춰 취소/대조합니다. 다른 coordinator의 기본5초, freshness/TTL/시각/스냅샷/SLTP 판단은 유지합니다. 유휴 중 TP 체결/외부 변화를 주기 조회로 발견하는 시간은 길어질 수 있으며, 거래소 SL/TP는 계속 독립 동작합니다. 오류나 미체결을 성공으로 바꾸지 않습니다.
- 정리의 첫 시각 API 호출 전에 로컬 진입 정지·stream/reconciled=false를 기록합니다. 제한 상태에서 노출이 남았다면 EMERGENCY로 기록하고 기존 손실 하드 락은 보존합니다. 거래소 제한으로 종료할 수 없으면 UNKNOWN flat과 `RATE_LIMITED_VERIFY_EXCHANGE_MANUALLY`로 명시하며 수동 확인이 필요합니다. 강제 API 우회·같은 주문 재전송은 하지 않습니다.
- HTTP418/429는 본문이 HTML/잘못된 JSON이어도 제한으로 처리합니다. 원래 최소60초 쿨다운보다 긴 Retry-After(초/HTTP 날짜)를 존중하며 기존 쿨다운을 줄이지 않습니다. 동일 클라이언트의 시각/공개 GET/listen-key/서명 요청도 제한 중에는 전송하지 않습니다.
- SDK/풀 전송에서 허용한 Retry-After와 X-MBX-USED-WEIGHT-1M만 보존합니다. 원문 헤더·키·서명·Cookie는 출력하지 않습니다. TP 결과와 설치 사후 결과에 HTTP 제한 상태, 관측 최대 가중치, 헤더 표본 수, 남은 쿨다운의 안전한 진단을 추가합니다.

기존 코드를 확인한 결과 운영 스냅샷은 이미 종료 이력 생략 규칙을 사용하고 있었습니다. 과거 주문 조회 방식이나 스냅샷 선택을 새로 바꾸지 않습니다. 핵심 DemoCoordinator/snapshot/일관성 판정/초기 체결 정착/flat 확인의 전체 이력 증거/위험·기간 기준금액/임대60초·DB잠금2초·fencing/주문 상한·SDK 쓰기 규칙은 보존합니다. 과거 전용 감사·복구는 여전히 전체 이력 규칙과 기존 예산을 사용합니다.

## 설치 검사와 한계

실제 설치 전후 각각 29개 파일의 일관된 버전 집합을 확인합니다. 이전 버전 전체 또는 새 버전 전체만 사전 검사에 허용하고, 사후 검사는 새 버전만 허용합니다. 파일을 섞으면 차단합니다. 로컬 원장/owner/fence/lease, 적용된 수동 종료 영수증과 정확한 수수료 포함 손익 `-0.07032487`, 실패 마커, 기간/손실/입출금/레버리지, 두 운영 스냅샷의 안정성과 모든 종목의 빈 포지션/주문을 확인합니다. 검토 중 상태가 바뀌면 차단합니다. 이 검사는 새 주문·복구·과거 미해결 요청 승인 없이 수행합니다.

이번 변경은 요청량 완화와 제한 시 안전 상태/진단 보강입니다. 당시 관측은 약54.8초 동안 GET397건, RATE_LIMITED/PING, 자동 종료 요청0, flat UNKNOWN이었습니다. 이전 전송이 HTTP 상태/서버 가중치/Retry-After를 보고하지 않아 정확한 한도 원인은 아직 확정하지 못했습니다. 이번에도 공유 IP/다른 프로세스의 사용량을 추적하거나 전역 요청 가중치를 예약하는 제어기는 없습니다. 클라이언트 쿨다운은 프로세스 재시작을 넘어 공유되지 않습니다. 실제429 방지나 TP 체결을 보장하지 않습니다.

설치 후 먼저 읽기 전용 진단/가중치를 확인해야 합니다. 이 패키지의 설치 명령에는 새 DEMO 주문이 포함되지 않습니다. 이번 실패·수동 종료 이력과 성공했던 보호 자식 SIGKILL 이력은 그대로이며, 실제 부분체결 보호/T06 TP 체결 및 원래 SL 정리/Phase4 전체 수락은 여전히 미완료입니다. LIVE와 다음 Phase로 넘어가지 않습니다.
