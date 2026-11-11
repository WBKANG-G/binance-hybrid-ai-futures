@echo off
cd /d "%~dp0"
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; if((Get-FileHash -Algorithm SHA256 -LiteralPath 'review_runtime_deployment.py').Hash.ToLower() -ne 'df0645fee23ae7e9ccc896e8443fe517d6c5463c56ad9d2dc2aa06dc286d8859'){throw 'DEPLOYMENT_REVIEW_HASH_MISMATCH'}"
if errorlevel 1 exit /b 1
docker exec -i hybrid-windows-api-1 python - < review_runtime_deployment.py
exit /b %errorlevel%
