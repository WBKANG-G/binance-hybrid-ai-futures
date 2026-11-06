@echo off
setlocal
cd /d "%~dp0"
if exist serialized-protected-worker-result.log (
  echo BLOCKED: previous result exists. Preserve it; do not repeat this trial.
  exit /b 2
)
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'run_serialized_worker.py').Hash.ToLower() -ne '35648812cb8b5e77c208d1f8ead19b887857ec3a45a23e11b9497f7c74ea77e6'){throw 'RUNNER_SOURCE_MISMATCH'}"
if errorlevel 1 exit /b 2
echo One NEW capped DEMO LIMIT entry; at most one protected child kill.
echo Quantity cap 0.0007 BTC; notional cap 100 USDT. No automatic retry or resume.
echo Keep this window and Docker running. Checks and recovery may take several minutes.
echo Output will appear after the supervisor returns; do not close this window.
docker exec -i hybrid-windows-api-1 python - < run_serialized_worker.py > serialized-protected-worker-result.log
set "TRIAL_EXIT=%errorlevel%"
type serialized-protected-worker-result.log
if not "%TRIAL_EXIT%"=="0" echo NOT VERIFIED: preserve this log and do not rerun the trial.
exit /b %TRIAL_EXIT%
