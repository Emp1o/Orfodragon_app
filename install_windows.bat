@echo off
chcp 65001 > nul
title ОрфоДракон — Установка
echo.
echo ============================================
echo     ОрфоДракон — Установка
echo     Тренажёр ударений и запятых
echo ============================================
echo.

cd /d "%~dp0"
set APP_DIR=%~dp0

:: ── 1. Проверяем Python ──────────────────────────────────────────
set PYTHON=
for %%p in (python3.exe python.exe py.exe) do (
    where %%p > nul 2>&1
    if not errorlevel 1 (
        %%p --version > nul 2>&1
        if not errorlevel 1 (
            set PYTHON=%%p
            goto :python_found
        )
    )
)

:: Проверяем стандартные пути
for %%d in (
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
    "C:\Python310\python.exe"
) do (
    if exist %%d (
        set PYTHON=%%d
        goto :python_found
    )
)

:: Python не найден — скачиваем через winget или предлагаем скачать
echo [!] Python не найден на вашем компьютере.
echo.
echo Установите Python одним из способов:
echo.
echo  Способ 1 (рекомендуется для Windows 10/11):
echo    Откройте Microsoft Store и найдите "Python 3.12"
echo    https://www.microsoft.com/store/productId/9NCVDN91XZQP
echo.
echo  Способ 2:
echo    Скачайте с python.org:
echo    https://www.python.org/downloads/windows/
echo    ВАЖНО: при установке поставьте галочку "Add Python to PATH"
echo.
echo  Способ 3 (если есть winget):
echo    winget install -e --id Python.Python.3.12
echo.

set /p CHOICE="Открыть Microsoft Store сейчас? [д/н]: "
if /i "%CHOICE%"=="д" start "" "ms-windows-store://pdp/?ProductId=9NCVDN91XZQP"
if /i "%CHOICE%"=="y" start "" "ms-windows-store://pdp/?ProductId=9NCVDN91XZQP"

echo.
echo После установки Python запустите этот файл снова.
pause
exit /b 1

:python_found
echo [OK] Python найден: %PYTHON%
%PYTHON% --version
echo.

:: ── 2. Проверяем/устанавливаем pip ──────────────────────────────
echo Проверка зависимостей...
%PYTHON% -m pip --version > nul 2>&1
if errorlevel 1 (
    echo Устанавливаем pip...
    %PYTHON% -m ensurepip --upgrade
)

:: Нет зависимостей! Всё работает на стандартной библиотеке Python.
echo [OK] Всё готово! Дополнительные пакеты не нужны.
echo.

:: ── 3. Создаём ярлык на рабочем столе ───────────────────────────
echo Создание ярлыка на рабочем столе...

set DESKTOP=%USERPROFILE%\Desktop
if not exist "%DESKTOP%" set DESKTOP=%USERPROFILE%\Рабочий стол

:: Используем PowerShell для создания ярлыка (надёжнее VBScript)
powershell -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell; ^
   $s = $ws.CreateShortcut('%DESKTOP%\ОрфоДракон.lnk'); ^
   $s.TargetPath = '%PYTHON%'; ^
   $s.Arguments = '\"%APP_DIR%main.py\"'; ^
   $s.WorkingDirectory = '%APP_DIR%'; ^
   $s.Description = 'ОрфоДракон — тренажёр ударений'; ^
   $s.Save()" 2> nul

if exist "%DESKTOP%\ОрфоДракон.lnk" (
    echo [OK] Ярлык создан на рабочем столе
) else (
    echo [!] Ярлык не создан автоматически.
    echo     Для запуска используйте файл: %APP_DIR%ОрфоДракон.bat
)

:: ── 4. Проверяем запуск ─────────────────────────────────────────
echo.
echo Проверяем работу приложения...
%PYTHON% -c "import http.server, json, sqlite3, re, threading, webbrowser; print('OK')" 2>&1
if errorlevel 1 (
    echo [ОШИБКА] Python работает некорректно
    pause
    exit /b 1
)
echo [OK] Все модули работают

echo.
echo ============================================
echo     Установка завершена успешно!
echo ============================================
echo.
echo Приложение работает в браузере (Chrome, Firefox, Edge).
echo Данные хранятся локально на вашем компьютере.
echo.

set /p RUN="Запустить ОрфоДракон сейчас? [д/н]: "
if /i "%RUN%"=="д" (
    start "" "%PYTHON%" "%APP_DIR%main.py"
)
if /i "%RUN%"=="y" (
    start "" "%PYTHON%" "%APP_DIR%main.py"
)
if "%RUN%"=="" (
    start "" "%PYTHON%" "%APP_DIR%main.py"
)

echo.
echo Для повторного запуска используйте ярлык на рабочем столе
echo или файл ОрфоДракон.bat
echo.
pause
