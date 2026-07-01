@echo off
rem 每日連結稽核排程進入點（Windows 工作排程器呼叫）
cd /d "%~dp0.."
set PYTHONPATH=%cd%
set PYTHONIOENCODING=utf-8
if not exist private\logs mkdir private\logs
for /f "tokens=1-3 delims=/- " %%a in ("%date%") do set TODAY=%%a%%b%%c
cd daily
python batch_audit.py --daily >> ..\private\logs\scan_%TODAY%.log 2>&1
