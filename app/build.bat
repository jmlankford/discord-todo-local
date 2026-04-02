@echo off
title Discord To-Do — Builder
echo ============================================
echo  Discord To-Do Widget — Build Script
echo ============================================
echo.

echo [1/3] Installing Python dependencies...
pip install requests apscheduler pytz pyinstaller
if %errorlevel% neq 0 (
    echo.
    echo ERROR: pip install failed. Make sure Python is installed and in your PATH.
    pause
    exit /b 1
)

echo.
echo [2/3] Building exe with PyInstaller...
python -m PyInstaller ^
    --onefile ^
    --windowed ^
    --name "DiscordTodo" ^
    --hidden-import=apscheduler.schedulers.background ^
    --hidden-import=apscheduler.triggers.cron ^
    --hidden-import=apscheduler.executors.pool ^
    --hidden-import=apscheduler.jobstores.memory ^
    --hidden-import=pytz ^
    discord_todo.py

if %errorlevel% neq 0 (
    echo.
    echo ERROR: PyInstaller build failed. See output above.
    pause
    exit /b 1
)

echo.
echo [3/3] Copying config file to dist folder...
if exist config.json (
    copy /Y config.json dist\config.json >nul
    echo     config.json copied to dist\
) else (
    echo     No config.json found — the app will show the setup screen on first run.
)

echo.
echo ============================================
echo  BUILD COMPLETE!
echo ============================================
echo.
echo  Your exe is ready at:  dist\DiscordTodo.exe
echo.
echo  To run: open the dist\ folder and double-click DiscordTodo.exe
echo  Note:   config.json must stay in the same folder as the exe.
echo.
pause
