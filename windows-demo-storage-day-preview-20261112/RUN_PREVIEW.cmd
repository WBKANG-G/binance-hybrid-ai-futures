@echo off
cd /d "%~dp0"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'TRANSFER_MANIFEST.json').Hash.ToLower() -ne 'f37ca690533f4d596f86db3d30ff967f7f79f73b31cb8f5e982ae8b7cac1308b'){throw 'TRANSFER_MANIFEST_HASH_MISMATCH'}; $m=Get-Content -Raw -LiteralPath 'TRANSFER_MANIFEST.json' | ConvertFrom-Json; foreach($p in $m.files.PSObject.Properties){if((Get-FileHash -Algorithm SHA256 -LiteralPath $p.Name).Hash.ToLower() -ne $p.Value){throw 'TRANSFER_FILE_HASH_MISMATCH'}}; Write-Output 'TRANSFER_FILES_VERIFIED'"
if errorlevel 1 exit /b 2
docker exec -i hybrid-windows-api-1 python - --attest-no-activity-since-manual-close --attested-day-kst 2026-11-12 < preview_storage_day_20261112.py
exit /b %errorlevel%
