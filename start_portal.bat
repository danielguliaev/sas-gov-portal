@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Правовая библиотека — локальный запуск

rem This launcher MUST be inside the sas-gov-portal project directory.
cd /d "%~dp0"
rem A downloaded launcher may remain in Downloads; look for the known project folder.
if not exist "pyproject.toml" (
    if exist "%USERPROFILE%\sas-gov-portal\pyproject.toml" cd /d "%USERPROFILE%\sas-gov-portal"
)
if not exist "pyproject.toml" goto wrong_folder
if not exist "app\main.py" goto wrong_folder

rem Use a dedicated virtual environment; an older broken .venv is not touched.
set "ENV_DIR=%CD%\.venv-portal"
set "ENV_PY=%ENV_DIR%\Scripts\python.exe"
set "SITE_URL=http://127.0.0.1:8000/"
rem Import bundled Penal Code Part I once into the local development library.
set "AUTO_IMPORT_PENAL_PART_I=1"

echo ===============================================
echo     Правовая библиотека штата Сан Андреас
echo ===============================================
echo.

if exist "%ENV_PY%" (
    "%ENV_PY%" -c "import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] <= (3, 12) else 1)" >nul 2>&1
    if errorlevel 1 goto invalid_env
)

if not exist "%ENV_PY%" (
    call :find_python
    if errorlevel 1 goto no_python
    echo [1/3] Создаётся окружение Python...
    call :create_env
    if errorlevel 1 goto venv_error
)

if not exist "%ENV_PY%" goto venv_error

if exist "%ENV_DIR%\.deps-ready" (
    "%ENV_PY%" -c "import fastapi, uvicorn, bleach, jwt" >nul 2>&1
    if errorlevel 1 del /q "%ENV_DIR%\.deps-ready" >nul 2>&1
)

if not exist "%ENV_DIR%\.deps-ready" (
    echo [2/3] Устанавливаются зависимости. Это нужно только при первом запуске.
    "%ENV_PY%" -m pip install --disable-pip-version-check --upgrade pip setuptools wheel
    if errorlevel 1 goto install_error
    "%ENV_PY%" -m pip install --disable-pip-version-check --only-binary=:all: -e .
    if errorlevel 1 goto install_error
    > "%ENV_DIR%\.deps-ready" echo OK
)

rem If this portal is already running, just open it in the browser.
powershell.exe -NoProfile -Command "try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 2 -ErrorAction Stop; if ($r.status -eq 'ok') { exit 0 } } catch {}; exit 1" >nul 2>&1
if not errorlevel 1 (
    echo Портал уже запущен. Открываем браузер...
    start "" "%SITE_URL%"
    exit /b 0
)

echo [3/3] Запуск сервера. Страница откроется автоматически.
echo Адрес: %SITE_URL%
echo Для остановки закройте это окно или нажмите Ctrl+C.
echo.

rem Wait for /health in the background, then open the default browser.
start "" /min powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "for ($i=0; $i -lt 120; $i++) { try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 2 -ErrorAction Stop; if ($r.status -eq 'ok') { Start-Process 'http://127.0.0.1:8000/'; break } } catch {}; Start-Sleep -Milliseconds 500 }"
"%ENV_PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
if errorlevel 1 goto server_error
exit /b 0

:create_env
%PYTHON_CMD% -m venv "%ENV_DIR%"
exit /b %ERRORLEVEL%

:find_python
py -3.12 -V >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=py -3.12"
    exit /b 0
)
py -3.11 -V >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=py -3.11"
    exit /b 0
)
python -c "import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] <= (3, 12) else 1)" >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=python"
    exit /b 0
)
exit /b 1

:wrong_folder
echo ОШИБКА: не найдена папка проекта sas-gov-portal.
echo Положите файл в папку проекта рядом с pyproject.toml или проверьте адрес.
goto fail
:no_python
echo ОШИБКА: не найден Python 3.12 или 3.11.
echo Установите Python 3.12 через PowerShell:
echo     winget install --id Python.Python.3.12 -e
echo После установки запустите этот файл снова.
goto fail
:invalid_env
echo ОШИБКА: окружение .venv-portal содержит неподходящую версию Python.
echo Переименуйте папку .venv-portal и запустите файл снова.
goto fail
:venv_error
echo ОШИБКА: не удалось создать виртуальное окружение.
echo Проверьте установленный Python и свободное место на диске.
goto fail
:install_error
echo ОШИБКА: зависимости не установились. Вывод ошибки указан выше.
echo Проверьте подключение к интернету и версию Python.
goto fail
:server_error
echo ОШИБКА: сервер не запустился. Возможно, порт 8000 уже занят.
goto fail
:fail
echo.
pause
exit /b 1
