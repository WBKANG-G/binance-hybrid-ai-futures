@echo off
setlocal
cd /d "%~dp0"
if exist last-exit-review.json (
  echo BLOCKED: last-exit-review.json already exists. Existing evidence will not be overwritten.
  exit /b 2
)
docker exec -i hybrid-windows-api-1 python - < review_last_safety_exit.py > last-exit-review.json
set "EXIT_REVIEW_STATUS=%errorlevel%"
type last-exit-review.json
exit /b %EXIT_REVIEW_STATUS%
