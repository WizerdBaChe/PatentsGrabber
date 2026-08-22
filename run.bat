@echo off
REM PatentsGrabber - Stage 0 launcher
cd /d "%~dp0"
echo Starting PatentsGrabber on http://127.0.0.1:8000
start "" http://127.0.0.1:8000
python run.py
pause
