@echo off
title Elysian Rebuild Update
cd /d "C:\Users\Peter\Documents\Elysian"

echo ========================================
echo Elysian Launcher Update
echo ========================================

echo Backing up .env...
if exist "C:\Users\Peter\Documents\Elysian\.env" (
    copy /Y "C:\Users\Peter\Documents\Elysian\.env" "C:\Users\Peter\Documents\Elysian\elysian_env_backup.tmp" ^>nul
) else (
    echo Warning: No .env file found to back up.
)

echo Closing current launcher process...
taskkill /f /pid 45032 ^>nul 2^>^&1

echo Closing any leftover Elysian launcher processes...
taskkill /f /im Elysian.exe ^>nul 2^>^&1

echo Waiting for Windows file locks to release...
timeout /t 8 /nobreak ^>nul

echo Cleaning old build folders...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist Elysian.spec del /f /q Elysian.spec

echo.
echo Rebuilding Elysian.exe from latest elysian.py...
"python" -m PyInstaller --onedir --windowed --clean --noconfirm --name Elysian "C:\Users\Peter\Documents\Elysian\elysian.py"

if errorlevel 1 (
    echo.
    echo Build failed. The old launcher was not replaced.
    echo If the error mentions Access is denied, close every Elysian.exe window and try again.
    echo Your backed up .env is here if needed:
    echo C:\Users\Peter\Documents\Elysian\elysian_env_backup.tmp
    pause
    exit /b 1
)

echo.
echo Build completed successfully.

echo Restoring .env to install directory...
if exist "C:\Users\Peter\Documents\Elysian\elysian_env_backup.tmp" (
    copy /Y "C:\Users\Peter\Documents\Elysian\elysian_env_backup.tmp" "C:\Users\Peter\Documents\Elysian\dist\Elysian\.env" ^>nul
    del /f /q "C:\Users\Peter\Documents\Elysian\elysian_env_backup.tmp" ^>nul 2^>^&1
) else (
    echo Warning: No .env backup existed to restore.
)

if exist "C:\Users\Peter\Documents\Elysian\dist\Elysian\Elysian.exe" (
    echo Starting updated Elysian launcher...
    start "" "C:\Users\Peter\Documents\Elysian\dist\Elysian\Elysian.exe"
    exit /b 0
) else (
    echo Updated EXE was not found at:
    echo C:\Users\Peter\Documents\Elysian\dist\Elysian\Elysian.exe
    pause
    exit /b 1
)
