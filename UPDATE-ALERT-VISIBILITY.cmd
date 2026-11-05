@echo off
setlocal
cd /d "%~dp0"
echo Updating read-only alert visibility. No new mail or order submission.
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $m=Get-Content -Raw 'TRANSFER_MANIFEST.json' | ConvertFrom-Json; foreach($p in $m.files.PSObject.Properties){if((Get-FileHash -Algorithm SHA256 -LiteralPath $p.Name).Hash.ToLower() -ne $p.Value){throw 'SOURCE_HASH_MISMATCH'}}; Write-Output 'TRANSFER_FILES_VERIFIED'"
if errorlevel 1 exit /b 2
docker exec hybrid-windows-api-1 python -m scripts.check_demo_session_ready
if errorlevel 1 exit /b 2
docker compose -p hybrid-windows -f infra/docker-compose.yml up -d --build --no-deps --wait api web
if errorlevel 1 exit /b 2
docker exec hybrid-windows-api-1 python -m scripts.verify_demo_alert_visibility
if errorlevel 1 exit /b 2
docker exec hybrid-windows-web-1 node -e "const fs=require('node:fs'),c=require('node:crypto'),e={'src/App.tsx': 'b2d8d32a18b0d7d207dcdc96e5197eebcfeece33a45a8931a4a4f68d279e24c0', 'src/AlertStatusPanel.tsx': '843ef99c61ff8a061d10a1525d13c04ee62f577b4393a3ef033a368d7914141c', 'src/style.css': 'd9ef3031a076494211d8735760f20a91daed9c9d425e2c4e5100314ad04eca62'};if(!Object.entries(e).every(([n,h])=>c.createHash('sha256').update(fs.readFileSync(n)).digest('hex')===h))process.exit(2);fetch('http://127.0.0.1:5173/api/v1/demo/alerts/status').then(async r=>{if(!r.ok)throw Error();const s=await r.json();if(s.status!=='READ_ONLY_ALERT_STATUS'||s.orders_enabled!==false||s.live_trading_enabled!==false)throw Error();console.log('WEB_SOURCE_AND_ALERT_PROXY_VERIFIED')}).catch(()=>process.exit(2))"
if errorlevel 1 exit /b 2
docker exec hybrid-windows-api-1 python -m scripts.check_demo_session_ready
if errorlevel 1 exit /b 2
echo ALERT_VISIBILITY_UPDATED_ENTRIES_STILL_PAUSED
echo Open http://127.0.0.1:5173 and refresh with Ctrl+F5.
exit /b 0
