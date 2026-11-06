@echo off
setlocal
cd /d "%~dp0"
if exist protected-ready-review.json (
  echo BLOCKED: existing report will not be overwritten.
  exit /b 2
)
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'inspect_protected_ready.py').Hash.ToLower() -ne '2f5e4e4e94b71c259710a933befe84453bcbd71420a37ca79108892117ca0390'){throw 'HELPER_HASH_MISMATCH'}"
if errorlevel 1 exit /b 2
docker exec -i hybrid-windows-api-1 python - < inspect_protected_ready.py > protected-ready-review.json
set "REVIEW_EXIT=%errorlevel%"
docker exec -i hybrid-windows-api-1 python -c "import json,sys; r=json.load(sys.stdin); fs=[{'at_utc':e.get('at_utc'),'fence':e.get('fence'),'failure':e.get('session_failure')} for e in r.get('timeline',[]) if e.get('kind')=='DEMO_PRIVATE_SESSION_FAILED']; print(json.dumps({'diagnostic':r.get('protected_ready_diagnostic'),'ready_marker_recorded':r.get('ready_marker_recorded'),'finally_marker_recorded':r.get('finally_marker_recorded'),'leader_released':r.get('leader_released'),'current_local_state':r.get('current_local_state'),'private_session_failure_events':fs,'timeline_count':r.get('timeline_count'),'timeline_truncated':r.get('timeline_truncated'),'exchange_checked':r.get('exchange_checked'),'database_changed_by_diagnostic':r.get('database_changed_by_diagnostic')}))" < protected-ready-review.json
echo Full scoped timeline saved in protected-ready-review.json
exit /b %REVIEW_EXIT%
