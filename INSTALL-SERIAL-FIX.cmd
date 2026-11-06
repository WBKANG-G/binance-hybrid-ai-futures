@echo off
setlocal
cd /d "%~dp0"
echo Install code fix only. No new entry, child kill or recovery acknowledgement.
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $m=Get-Content -Raw 'TRANSFER_MANIFEST.json' | ConvertFrom-Json; foreach($p in $m.files.PSObject.Properties){if((Get-FileHash -Algorithm SHA256 -LiteralPath $p.Name).Hash.ToLower() -ne $p.Value){throw 'SOURCE_HASH_MISMATCH'}}; Write-Output 'TRANSFER_FILES_VERIFIED'"
if errorlevel 1 exit /b 2
docker exec -i hybrid-windows-api-1 python - --phase pre < guard_serial_fix_install.py
if errorlevel 1 exit /b 2
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --no-deps --wait api
if errorlevel 1 exit /b 2
docker exec hybrid-windows-api-1 python -c "import hashlib; from pathlib import Path; expected={'services/execution/demo_protected_worker_check.py': '9c264588fa7802efc0dcaa4f923153072fa85ba6fd888a23a2dda8ede4966238', 'packages/persistence/demo.py': '40f9a33b8eeda28c96f36de35656e4667a2d2461ce9de79dc4b19a687cd5cd5a', 'services/execution/demo_stream.py': '409098cb05abbfb97eb9b9f1db2070ca6bb5782bc6dd7542311e4eb703b6f456', 'scripts/review_demo_recovery.py': 'dad0a79f584f8b660b0240254d03666a8bc12bbdbaa7073ba7843824c913e675'}; assert all(hashlib.sha256(Path(n).read_bytes()).hexdigest()==h for n,h in expected.items()), 'RUNTIME_SOURCE_MISMATCH'; print('SERIAL_FIX_SOURCE_VERIFIED_ORIGINAL_LOCK_AND_STREAM_UNCHANGED')"
if errorlevel 1 exit /b 2
docker exec -i hybrid-windows-api-1 python - --phase post < guard_serial_fix_install.py
if errorlevel 1 exit /b 2
echo SERIAL_FIX_INSTALLED_NO_NEW_TRIAL_ENTRIES_STILL_PAUSED
exit /b 0
