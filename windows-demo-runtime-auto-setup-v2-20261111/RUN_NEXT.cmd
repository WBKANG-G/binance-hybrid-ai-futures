@echo off
cd /d "%~dp0"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'TRANSFER_MANIFEST.json').Hash.ToLower() -ne '9ae6608ab5d2621811c395a717540f129fb2aa6c42744bc9ee1ccf0ebdfafbc8'){throw 'TRANSFER_MANIFEST_HASH_MISMATCH'}; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'RUN_NEXT.ps1').Hash.ToLower() -ne 'c1dbbe4327c33a3f2cae5ad75107b87932466bbde6d24a5c835831710fd3442b'){throw 'RUNNER_HASH_MISMATCH'}"
if errorlevel 1 exit /b 1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0RUN_NEXT.ps1"
exit /b %errorlevel%
