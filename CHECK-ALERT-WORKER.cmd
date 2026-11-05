@echo off
setlocal
cd /d "%~dp0"
echo Read-only worker observation. No restart, new alert, mail or order.
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'inspect_alert_worker.py').Hash.ToLower() -ne '3e88359f1e945a4b666fc8efb54eecfa31b4680eda3bb3c805c8e47bb94dfc09'){throw 'HELPER_HASH_MISMATCH'}"
if errorlevel 1 exit /b 2
docker inspect --format "worker_container: {{.State.Status}} / {{.State.Health.Status}}" hybrid-windows-alerts-1
if errorlevel 1 exit /b 2
docker exec -i hybrid-windows-alerts-1 python - < inspect_alert_worker.py
exit /b %errorlevel%
