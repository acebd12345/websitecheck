@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set "ROOT=%~dp0"
set "PYTHONPATH=%ROOT%"
cd /d "%ROOT%daily"
echo ============================================
echo  External link audit (daily / link_audit)
echo ============================================
python batch_audit.py %*
pause
