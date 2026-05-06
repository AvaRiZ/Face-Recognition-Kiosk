@echo off
setlocal
set REPO_ROOT=%~dp0..\..
set LOG_DIR=%LOCALAPPDATA%\FaceRecognitionKiosk\logs
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set LAUNCHER_LOG=%LOG_DIR%\launcher.log
pushd "%REPO_ROOT%"

echo.>> "%LAUNCHER_LOG%"
echo === Launch at %DATE% %TIME% ===>> "%LAUNCHER_LOG%"

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m app.windows_launcher >> "%LAUNCHER_LOG%" 2>&1
) else (
  python -m app.windows_launcher >> "%LAUNCHER_LOG%" 2>&1
)

if errorlevel 1 (
  echo Startup failed. Check: "%LAUNCHER_LOG%"
  type "%LAUNCHER_LOG%"
  pause
)

popd
endlocal
