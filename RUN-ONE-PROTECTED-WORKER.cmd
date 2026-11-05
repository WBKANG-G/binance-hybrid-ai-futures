@echo off
setlocal
cd /d "%~dp0"
if exist protected-worker-result.log (
  echo BLOCKED: prior execution log exists. Do not repeat this operation.
  exit /b 2
)
echo This command runs the approved ONE capped DEMO LIMIT entry and ONE protected child kill.
echo Quantity cap 0.0007 BTC; notional cap 100 USDT. Keep this window and Docker running.
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $m=Get-Content -Raw 'TRANSFER_MANIFEST.json' | ConvertFrom-Json; foreach($p in $m.files.PSObject.Properties){if((Get-FileHash -Algorithm SHA256 -LiteralPath $p.Name).Hash.ToLower() -ne $p.Value){throw 'SOURCE_HASH_MISMATCH'}}; Write-Output 'TRANSFER_FILES_VERIFIED'"
if errorlevel 1 exit /b 2
docker exec hybrid-windows-api-1 python -m scripts.check_demo_session_ready
if errorlevel 1 exit /b 2
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --no-deps --wait api
if errorlevel 1 exit /b 2
docker exec hybrid-windows-api-1 python -c "import hashlib; from pathlib import Path; expected={'scripts/check_demo_protected_worker.py': 'b06813e8ffe64e4ad5a89700d5610e612a9cc1eb44b3b3396f397c5e7abf58ee', 'services/execution/demo_protected_worker_check.py': '0e7027450669a7b4ef5467a5b6e6eb21ddfca0a2430d68ac25025076e31c0b79', 'services/execution/demo.py': 'deb90dd8ddd9df627054a11c76f8dd8b09e364f2aaaca8f82b7fd0c237c444ab', 'services/execution/demo_stream.py': '409098cb05abbfb97eb9b9f1db2070ca6bb5782bc6dd7542311e4eb703b6f456', 'services/execution/demo_validation.py': 'd703860caf01135b3f5e82a444adc5afde1795e1f4d48bbd515cc4b9de5666a0', 'services/execution/demo_flat_transport.py': '93f878c9869ad397a52546f0aa976bbe8071366eff36c7d0be6b1329df757401', 'packages/persistence/demo.py': '40f9a33b8eeda28c96f36de35656e4667a2d2461ce9de79dc4b19a687cd5cd5a'}; assert all(hashlib.sha256(Path(n).read_bytes()).hexdigest()==h for n,h in expected.items()), 'SOURCE_HASH_MISMATCH'; print('PROTECTED_WORKER_RUNTIME_SOURCE_VERIFIED')"
if errorlevel 1 exit /b 2
docker exec hybrid-windows-api-1 python -m scripts.check_demo_session_ready
if errorlevel 1 exit /b 2
powershell -NoProfile -Command "& docker exec hybrid-windows-api-1 python -m scripts.check_demo_protected_worker --enable-demo --enable-protected-worker-kill --operation-id 5bb5814c-55e0-4b7f-882c-a7a449ec2182 | Tee-Object -FilePath 'protected-worker-result.log'; exit $LASTEXITCODE"
exit /b %errorlevel%
