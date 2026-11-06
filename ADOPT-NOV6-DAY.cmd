@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'adopt_nov6_day.py').Hash.ToLower() -ne 'd41dbc648bf4a6246e5db305f024ed5995ed690a0571e0fbc255011f4ca7b2b9'){throw 'HELPER_HASH_MISMATCH'}"
if errorlevel 1 exit /b 2
docker exec -i hybrid-windows-api-1 python - < adopt_nov6_day.py
exit /b %errorlevel%
