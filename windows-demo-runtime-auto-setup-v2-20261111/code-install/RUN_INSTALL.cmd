@echo off
cd /d "%~dp0"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'TRANSFER_MANIFEST.json').Hash.ToLower() -ne '4f5ea3e905322ec19b7cbac1b8844e6f1369620ed12cc4dd0c5246f7b12271b8'){throw 'TRANSFER_MANIFEST_HASH_MISMATCH'}; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'INSTALL_CODE.ps1').Hash.ToLower() -ne '94039c97de28a94fb7dcc59c999623bf218d04bee6574ec3e4b2974e5fb4809c'){throw 'INSTALLER_HASH_MISMATCH'}"
if errorlevel 1 exit /b 1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0INSTALL_CODE.ps1"
exit /b %errorlevel%
