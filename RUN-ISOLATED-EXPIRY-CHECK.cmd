@echo off
setlocal
cd /d "%~dp0"
docker image inspect hybrid-phase4-fault-check:local >nul 2>&1
if errorlevel 1 (
  echo BLOCKED: existing isolated fault-check image not found.
  exit /b 2
)
docker compose -p hybrid-phase4-expiry-check -f docker-compose.expiry-check.yml up -d --wait --pull never fault-db
if errorlevel 1 (
  set "CHECK_EXIT=2"
  goto cleanup
)
docker compose -p hybrid-phase4-expiry-check -f docker-compose.expiry-check.yml run --pull never --rm --no-deps -T --entrypoint python checks - < run_isolated_expiry_check.py
set "CHECK_EXIT=%errorlevel%"
:cleanup
docker compose -p hybrid-phase4-expiry-check -f docker-compose.expiry-check.yml down
if errorlevel 1 exit /b 2
exit /b %CHECK_EXIT%
