@echo off
setlocal
cd /d "%~dp0"
echo ====================================================
echo     RETAIL INTELLIGENCE - LOCAL DASHBOARD
echo ====================================================
if not exist ".venv\Scripts\python.exe" (
    echo Creating isolated Python environment...
    py -3 -m venv .venv
    if errorlevel 1 (
        echo Python 3 not found. Install Python 3.11+ then retry.
        pause
        exit /b 1
    )
)
echo Installing dependencies if required...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Installation failed. Check internet access and Python installation.
    pause
    exit /b 1
)
echo.
echo Opening dashboard at http://localhost:8501
echo Choose CSV for zero-database setup, or MySQL if configured.
".venv\Scripts\python.exe" -m streamlit run app.py
pause
