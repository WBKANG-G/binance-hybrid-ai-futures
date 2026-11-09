# Windows 코드 설치 — 진입 일시정지 유지

ZIP을 바탕화면에 푼 뒤 **CMD**에서 실행하세요. 아래 한 줄은 파일 검증 → 읽기 전용 사전 검사 → API만 갱신 → 사전 결과와 결합된 사후 검사입니다. 새 주문·기간 기준값 변경·거래 재개는 없습니다. 오류 시 이후 단계는 중단합니다. PowerShell5.1 문법으로 작성했지만 실제 Windows에서 실행하지는 않았습니다.

```cmd
cd /d "%USERPROFILE%\Desktop\windows-demo-tp-budget-install"
```

아래 긴 명령은 **한 줄 전체**를 복사하세요.

```cmd
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'TRANSFER_MANIFEST.json').Hash.ToLower() -ne '1c03ef7533a21ba08aff6f5352246c8673a44c603d019136419114b47f1b475a'){throw 'MANIFEST_HASH_MISMATCH'}; $m=Get-Content -Raw 'TRANSFER_MANIFEST.json' | ConvertFrom-Json; foreach($f in $m.files.PSObject.Properties){if((Get-FileHash -Algorithm SHA256 -LiteralPath $f.Name).Hash.ToLower() -ne $f.Value){throw 'TRANSFER_FILE_HASH_MISMATCH'}}; Write-Output 'TRANSFER_FILES_VERIFIED'; $raw=cmd /c 'docker exec -i hybrid-windows-api-1 python - --phase pre < guard_tp_budget_install.py'; $preExit=$LASTEXITCODE; $raw | Write-Output; if($preExit -ne 0){throw 'PRECHECK_BLOCKED_KEEP_PAUSED'}; $p=$raw | ConvertFrom-Json; if($p.tp_budget_install -ne 'PRECHECKED_BUDGET_CODE_ONLY_KEEP_PAUSED' -or $p.account_digest -notmatch '^[0-9a-f]{64}$' -or $p.exchange_digest -notmatch '^[0-9a-f]{64}$' -or ($p.pair_signed_reads -isnot [int] -and $p.pair_signed_reads -isnot [long]) -or $p.pair_signed_reads -le 2 -or $p.pair_signed_reads -gt 64 -or ($p.pair_signed_reads % 2) -ne 0){throw 'PRECHECK_BINDING_INVALID'}; $p | ConvertTo-Json -Depth 12 | Set-Content -Encoding UTF8 'tp_budget_precheck.json'; docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --no-deps --wait api; if($LASTEXITCODE -ne 0){throw 'BUILD_OR_HEALTHCHECK_BLOCKED_KEEP_PAUSED'}; cmd /c ('docker exec -i hybrid-windows-api-1 python - --phase post --expected-account '+$p.account_digest+' --expected-remote '+$p.exchange_digest+' --pre-reads '+$p.pair_signed_reads+' < guard_tp_budget_install.py'); if($LASTEXITCODE -ne 0){throw 'POSTCHECK_BLOCKED_KEEP_PAUSED'}"
```

성공 표시는 `POSTCHECKED_BUDGET_CODE_ONLY_KEEP_PAUSED`입니다. 결과를 보존해 공유하세요. `real_execution_default_disabled=true`는 정상입니다. 아직 TP 시험이나 진입 재개 명령을 실행하지 않습니다.
