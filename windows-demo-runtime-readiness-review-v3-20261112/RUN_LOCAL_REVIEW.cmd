@echo off
cd /d "%~dp0"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'TRANSFER_MANIFEST.json').Hash.ToLower() -ne 'ce1f5718a17f24d12ac7fd29249d4327e2c6450d7eb2d4d8593a3c9d2e22d9a0'){throw 'TRANSFER_MANIFEST_HASH_MISMATCH'}; $m=Get-Content -Raw -LiteralPath 'TRANSFER_MANIFEST.json' | ConvertFrom-Json; foreach($p in $m.files.PSObject.Properties){if((Get-FileHash -Algorithm SHA256 -LiteralPath $p.Name).Hash.ToLower() -ne $p.Value){throw 'TRANSFER_FILE_HASH_MISMATCH'}}; Write-Output 'TRANSFER_FILES_VERIFIED'"
if errorlevel 1 exit /b 2
docker exec -i hybrid-windows-api-1 python - < review_runtime_readiness.py
exit /b %errorlevel%
