@echo off
cd /d "%~dp0"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'TRANSFER_MANIFEST.json').Hash.ToLower() -ne '842e708526eb27910d6cca00d9c1c99d3d7e534f2e873bc7785539f22db17c7e'){throw 'TRANSFER_MANIFEST_HASH_MISMATCH'}; $m=Get-Content -Raw -LiteralPath 'TRANSFER_MANIFEST.json' | ConvertFrom-Json; foreach($p in $m.files.PSObject.Properties){if((Get-FileHash -Algorithm SHA256 -LiteralPath $p.Name).Hash.ToLower() -ne $p.Value){throw 'TRANSFER_FILE_HASH_MISMATCH'}}; Write-Output 'TRANSFER_FILES_VERIFIED'"
if errorlevel 1 exit /b 2
docker exec -i hybrid-windows-api-1 python - --phase postcheck < adopt_storage_day_20261112.py
exit /b %errorlevel%
