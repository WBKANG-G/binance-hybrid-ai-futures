@echo off
setlocal
cd /d "%~dp0"
if exist public-liquidity-report.json (
  echo BLOCKED: previous public-liquidity-report.json exists. Evidence will not be overwritten.
  exit /b 2
)
docker exec -i hybrid-windows-api-1 python - < check_public_liquidity.py > public-liquidity-report.json
set "PUBLIC_CHECK_EXIT=%errorlevel%"
type public-liquidity-report.json
exit /b %PUBLIC_CHECK_EXIT%
