@echo off
docker exec hybrid-windows-api-1 python -m scripts.check_demo_session_ready
exit /b %errorlevel%
