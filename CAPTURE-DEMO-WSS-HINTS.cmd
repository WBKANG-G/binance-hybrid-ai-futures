@echo off
setlocal
cd /d "%~dp0"
if exist wss-hint-report.json (
  echo BLOCKED: wss-hint-report.json already exists. Existing evidence will not be overwritten.
  exit /b 2
)
docker exec -i hybrid-windows-api-1 python - < capture_demo_wss_hints.py > wss-hint-report.json
set "CAPTURE_EXIT=%errorlevel%"
type wss-hint-report.json
exit /b %CAPTURE_EXIT%
