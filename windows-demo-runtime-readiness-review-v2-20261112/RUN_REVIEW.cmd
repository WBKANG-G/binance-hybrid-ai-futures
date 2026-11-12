@echo off
cd /d "%~dp0"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'TRANSFER_MANIFEST.json').Hash.ToLower() -ne '4ca425b8f055f287d488e99020d50b5ce2b305666582ce8c57fed950c62f3e76'){throw 'TRANSFER_MANIFEST_HASH_MISMATCH'}; $m=Get-Content -Raw -LiteralPath 'TRANSFER_MANIFEST.json' | ConvertFrom-Json; foreach($p in $m.files.PSObject.Properties){if((Get-FileHash -Algorithm SHA256 -LiteralPath $p.Name).Hash.ToLower() -ne $p.Value){throw 'TRANSFER_FILE_HASH_MISMATCH'}}; Write-Output 'TRANSFER_FILES_VERIFIED'"
if errorlevel 1 exit /b 2
docker exec -i hybrid-windows-api-1 python - --collect-exchange-evidence < review_runtime_readiness.py
exit /b %errorlevel%
