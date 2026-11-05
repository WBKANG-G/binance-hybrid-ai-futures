@echo off
setlocal
cd /d "%~dp0"
docker exec -i hybrid-windows-api-1 python - < adopt_nov5_day.py
exit /b %errorlevel%
