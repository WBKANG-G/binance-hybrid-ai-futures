@echo off
cd /d "%~dp0"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'adopt_manual_close_day_20261111.py').Hash.ToLower() -ne '624eb1df83b8f8de95b27d52ac0f6bc152a97ea775c4356c17cab4b7081161f4'){throw 'DAY_APPLY_HASH_MISMATCH'}"
if errorlevel 1 exit /b 1
docker exec -i hybrid-windows-api-1 python - --apply --accept-operator-evidence --attest-no-activity-since-manual-close --attested-day-kst 2026-11-11 < adopt_manual_close_day_20261111.py
exit /b %errorlevel%
