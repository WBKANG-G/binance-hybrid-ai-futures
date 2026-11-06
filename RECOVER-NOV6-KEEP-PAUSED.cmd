@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'acknowledge_flat_recovery.py').Hash.ToLower() -ne 'e31ec2972c9c4083d6e11d7bd9bc518f19c6b15de17f41f385e49acf79f9986e'){throw 'WRAPPER_SOURCE_MISMATCH'}"
if errorlevel 1 exit /b 2
if exist nov6-flat-recovery-result.json (
  echo BLOCKED: previous result exists. No automatic repeat.
  exit /b 2
)
set "REFERENCE=%~dp0unresolved-review.json"
if not exist "%REFERENCE%" set "REFERENCE=%USERPROFILE%\Desktop\windows-demo-recovery-unresolved-details\unresolved-review.json"
if not exist "%REFERENCE%" (
  echo BLOCKED: approved unresolved-review.json not found. Place it beside this CMD file.
  exit /b 2
)
echo Acknowledging only the four reviewed historical unresolved requests.
echo Risk state may become NORMAL; entries stay paused. No exchange order writes.
docker exec -i hybrid-windows-api-1 python -c "import sys,json,os; from pathlib import Path; data=sys.stdin.buffer.read(65537); assert len(data)<=65536, 'REFERENCE_TOO_LARGE'; json.loads(data); os.umask(0o077); folder=Path('/tmp/flat-recovery-0f72dd2e9c5d4567aa469f3772f79ea7'); folder.mkdir(mode=0o700); (folder/'reference.json').write_bytes(data)" < "%REFERENCE%"
if errorlevel 1 exit /b 2
docker exec -i hybrid-windows-api-1 python -c "import sys,hashlib,os; from pathlib import Path; data=sys.stdin.buffer.read(131073); assert len(data)<=131072 and hashlib.sha256(data).hexdigest()=='62ef93891359953f6b9d9a6442833ee3adb508475d0faeb486ccdb2b261b7e1b', 'HELPER_SOURCE_MISMATCH'; os.umask(0o077); (Path('/tmp/flat-recovery-0f72dd2e9c5d4567aa469f3772f79ea7')/'review_unresolved_details.py').write_bytes(data)" < review_unresolved_details.py
if errorlevel 1 (
  set "ACK_EXIT=2"
  goto cleanup
)
docker exec -i hybrid-windows-api-1 python - --acknowledge-four-historical-unresolved < acknowledge_flat_recovery.py > nov6-flat-recovery-result.json
set "ACK_EXIT=%errorlevel%"
type nov6-flat-recovery-result.json
:cleanup
docker exec hybrid-windows-api-1 python -c "import shutil; shutil.rmtree('/tmp/flat-recovery-0f72dd2e9c5d4567aa469f3772f79ea7', ignore_errors=True)"
if errorlevel 1 exit /b 2
exit /b %ACK_EXIT%
