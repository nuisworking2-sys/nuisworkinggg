@echo off
cd /d %~dp0\server
call .venv\Scripts\activate.bat
uvicorn app:app --host 127.0.0.1 --port 8000
