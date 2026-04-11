$ErrorActionPreference = "Stop"
.\.venv\Scripts\python.exe -m trading_mvp.migrate
.\.venv\Scripts\python.exe workers\worker.py
