@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Сенат штата Сан-Андреас — локальный запуск
cd /d "%~dp0"
set "ENV_DIR=%CD%\.venv-portal"
set "ENV_PY=%ENV_DIR%\Scripts\python.exe"
set "SITE_URL=http://127.0.0.1:8001/senate"

echo ===============================================
echo     Портал Сената штата Сан-Андреас
echo ===============================================
echo.
git pull --ff-only origin feat/senate-portal
if errorlevel 1 echo Не удалось обновить ветку Сената. Продолжаем с текущей версией.

if not exist "%ENV_PY%" (
  py -3.12 -m venv "%ENV_DIR%"
  if errorlevel 1 py -3.11 -m venv "%ENV_DIR%"
  if errorlevel 1 goto python_error
)
"%ENV_PY%" -m pip install --disable-pip-version-check -e .
if errorlevel 1 goto install_error

powershell.exe -NoProfile -Command "try {$r=Invoke-RestMethod -Uri 'http://127.0.0.1:8001/health' -TimeoutSec 2; if($r.status -eq 'ok'){exit 0}}catch{};exit 1" >nul 2>&1
if not errorlevel 1 (
  start "" "%SITE_URL%"
  exit /b 0
)
start "" /min powershell.exe -NoProfile -Command "for($i=0;$i -lt 100;$i++){try{$r=Invoke-RestMethod -Uri 'http://127.0.0.1:8001/health' -TimeoutSec 2;if($r.status -eq 'ok'){Start-Process 'http://127.0.0.1:8001/senate';break}}catch{};Start-Sleep -Milliseconds 500}"
"%ENV_PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8001
exit /b 0

:python_error
echo Не найден Python 3.11 или 3.12. Установите Python и повторите запуск.
pause
exit /b 1
:install_error
echo Не удалось установить зависимости портала.
pause
exit /b 1
