@echo off
cd /d "%~dp0"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'TRANSFER_MANIFEST.json').Hash.ToLower() -ne '8928dffa971a364d6c8e7cf1483c3c2c126d7f78265d1ca9fca6f364b3b0eabb'){throw 'TRANSFER_MANIFEST_HASH_MISMATCH'}; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'RUN_RECOVER.ps1').Hash.ToLower() -ne 'ad164769e4e9637d57098cfa2ccdb23e0ee1e17c5e7faaff485f6261e494dc3b'){throw 'RUNNER_HASH_MISMATCH'}"
if errorlevel 1 exit /b 2
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0RUN_RECOVER.ps1"
exit /b %errorlevel%
