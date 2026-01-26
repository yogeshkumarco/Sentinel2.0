@echo off
REM Add the parent directory to PYTHONPATH so 'sentinel_v3' module can be found
set PYTHONPATH=%PYTHONPATH%;%cd%\..

echo Starting Sentinel v3...
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
