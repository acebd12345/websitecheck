@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set "ROOT=%~dp0"
set "PYTHONPATH=%ROOT%"
cd /d "%ROOT%monthly"
echo ============================================
echo  Monthly website check
echo ============================================

echo [0/3] sync master sheet ...
python sync_config.py
if errorlevel 1 (echo SYNC FAILED - check network / key / gspread & pause & exit /b 1)

echo [1/3] scan sites ...
python monthly_check.py %*
if errorlevel 1 (echo SCAN FAILED & pause & exit /b 1)

echo [2/3] build checklist ...
python update_excel.py
if errorlevel 1 (echo BUILD FAILED & pause & exit /b 1)

echo [3/3] AI node review ...
python node_check.py check
if errorlevel 1 (echo AI REVIEW FAILED ^(checklist already built^) & pause & exit /b 1)

echo Done. Output in private\checklist folder and private\reports
pause
