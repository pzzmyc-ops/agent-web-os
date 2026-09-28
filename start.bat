@echo off
cd /d "%~dp0"
set "NO_PROXY=127.0.0.1,localhost,0.0.0.0,::1"
set "no_proxy=127.0.0.1,localhost,0.0.0.0,::1"
python\python.exe server.py
pause
