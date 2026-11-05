@echo off
setlocal
cd /d "%~dp0"
if exist unresolved-review.json (
  echo BLOCKED: unresolved-review.json already exists. Existing evidence will not be overwritten.
  exit /b 2
)
docker exec -i hybrid-windows-api-1 python - < review_unresolved_details.py > unresolved-review.json
set "DETAIL_REVIEW_EXIT=%errorlevel%"
type unresolved-review.json
exit /b %DETAIL_REVIEW_EXIT%
