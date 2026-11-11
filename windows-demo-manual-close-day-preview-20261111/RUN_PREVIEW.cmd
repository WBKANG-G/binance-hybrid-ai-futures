@echo off
cd /d "%~dp0"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'preview_manual_close_day_20261111.py').Hash.ToLower() -ne 'e309978bd8bec2dbc408ec018cc3faed88bd5b0f7bf947318366e868b6ef23c0'){throw 'DAY_PREVIEW_HASH_MISMATCH'}"
if errorlevel 1 exit /b 1
docker exec -i hybrid-windows-api-1 python - --attest-no-activity-since-manual-close --attested-day-kst 2026-11-11 < preview_manual_close_day_20261111.py
exit /b %errorlevel%
