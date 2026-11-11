@echo off
cd /d "%~dp0"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'TRANSFER_MANIFEST.json').Hash.ToLower() -ne 'dbb58a2485b85b114b1d0b48b660c160f1b6368c496c228f065eb7e6cea8fc22'){throw 'TRANSFER_MANIFEST_HASH_MISMATCH'}"
if errorlevel 1 exit /b 1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0INSTALL_CODE.ps1"
exit /b %errorlevel%
