@echo off
setlocal
cd /d "%~dp0"
docker exec -i hybrid-windows-api-1 python - < inspect_demo_tp_timing.py
exit /b %errorlevel%
