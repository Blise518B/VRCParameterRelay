@echo off
rem Dev launcher — creates the venv on first run, then starts the app.
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
    echo Creating virtual environment...
    python -m venv .venv || goto :error
    .venv\Scripts\python.exe -m pip install --upgrade pip
    .venv\Scripts\python.exe -m pip install -r requirements.txt || goto :error
    goto :run
)

rem Re-check requirements on every start. Nothing happens when they are already
rem satisfied, but a raised floor in requirements.txt — an aiohttp security fix,
rem say — then reaches an existing checkout instead of sitting stale forever.
rem Being offline is not fatal here: the app still starts.
.venv\Scripts\python.exe -m pip install -q -r requirements.txt

:run
.venv\Scripts\python.exe -m vrc_parameter_relay %*
goto :eof

:error
echo.
echo Setup failed. Is Python 3.10+ installed and on PATH?
pause
