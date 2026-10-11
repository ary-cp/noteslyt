@echo off
echo Starting Frontend Build...
cd frontend
call npm install
call npm run build
cd ..

echo Starting Backend Server...
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
pause
