$ErrorActionPreference = "Stop"
.\.venv\Scripts\python.exe -m trading_mvp.migrate
.\.venv\Scripts\python.exe -m uvicorn trading_mvp.main:app --app-dir backend --host 0.0.0.0 --port 8000
