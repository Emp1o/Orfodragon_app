@echo off
chcp 65001 > nul
cd /d "%~dp0"
set DEFAULT_PORT=17832
set PYTHON=

for %%p in (pythonw.exe python3.exe python.exe) do (
    where %%p > nul 2>&1
    if not errorlevel 1 ( set PYTHON=%%p & goto :check_running )
)
for %%d in (
    "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\pythonw.exe"
    "C:\Python312\pythonw.exe" "C:\Python311\pythonw.exe"
) do ( if exist %%d ( set PYTHON=%%d & goto :check_running ) )
start "" "%~dp0install_windows.bat"
exit /b

:check_running
set PORT=%DEFAULT_PORT%
set PORT_FILE=%APPDATA%\OrfoDragon\port.txt
if exist "%PORT_FILE%" ( set /p PORT=<"%PORT_FILE%" )

curl -s "http://127.0.0.1:%PORT%/api/health" > nul 2>&1
if not errorlevel 1 ( start "" "http://127.0.0.1:%PORT%" & exit /b )

curl -s "http://127.0.0.1:%DEFAULT_PORT%/api/health" > nul 2>&1
if not errorlevel 1 ( start "" "http://127.0.0.1:%DEFAULT_PORT%" & exit /b )

start "" "%PYTHON%" "%~dp0main.py"
exit /b
