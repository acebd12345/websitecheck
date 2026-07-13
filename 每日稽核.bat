@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set "ROOT=%~dp0"
set "PYTHONPATH=%ROOT%"
cd /d "%ROOT%"
echo ============================================
echo  Full overnight deep scan + mail
echo  (daily/batch_audit retired, use engine)
echo ============================================
python -m engine.full_overnight --mail %*
pause
