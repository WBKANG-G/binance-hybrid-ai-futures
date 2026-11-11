@echo off
cd /d "%~dp0"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'TRANSFER_MANIFEST.json').Hash.ToLower() -ne '907324f101dfb4ed2f3642decbecdf98a7b8586362296a1cdb87344307ae55b5'){throw 'TRANSFER_MANIFEST_HASH_MISMATCH'}; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'INSTALL_CODE.ps1').Hash.ToLower() -ne 'abdfc596f0c6ac13acc062858ccf7ef44f453dec7048fac89c022093aff154bb'){throw 'INSTALLER_HASH_MISMATCH'}"
if errorlevel 1 exit /b 1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0INSTALL_CODE.ps1"
exit /b %errorlevel%
