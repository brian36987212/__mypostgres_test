@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

REM Define log path before WAIT_NETWORK so timeout messages are logged
set LOGDIR=d:\StockPulse\stocks_news\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
for /f %%D in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set TODAY=%%D
set LOGFILE=%LOGDIR%\crawler_daily_%TODAY%.log

REM Wait for network (up to 120 seconds)
set /a WAIT_COUNT=0
:WAIT_NETWORK
ping -n 1 8.8.8.8 >nul 2>&1
if %errorlevel%==0 goto NETWORK_OK
set /a WAIT_COUNT+=1
if %WAIT_COUNT% GEQ 24 (
    echo [%date% %time%] Network timeout, skipping >> "%LOGFILE%"
    exit /b 1
)
timeout /t 5 /nobreak >nul
goto WAIT_NETWORK
:NETWORK_OK

REM Disable sleep during execution
powercfg /change standby-timeout-ac 0

REM Load environment variables from .env (skip # comments and blank lines)
for /f "usebackq eol=# tokens=1,* delims==" %%A in ("d:\StockPulse\.env") do (
    set "%%A=%%B"
)

REM ============================================
REM  Daily crawlers: MID tier only
REM  (lower / rare moved to run_crawlers_weekly.bat)
REM  (hot tier + content + sentiment handled by run_crawlers_hot.bat below)
REM ============================================

REM Clear mid progress files so each run fetches fresh
del /q "d:\StockPulse\stocks_news\nstock\progress_nstock_mid.txt" 2>nul
del /q "d:\StockPulse\stocks_news\yahoo\progress_mid.txt" 2>nul
del /q "d:\StockPulse\stocks_news\cnyes\progress_cnyes_mid.txt" 2>nul

echo ============================================ >> "%LOGFILE%"
echo [%date% %time%] Daily (mid) crawlers starting >> "%LOGFILE%"
echo ============================================ >> "%LOGFILE%"

REM --- Mid tier ---
cd /d d:\StockPulse\stocks_news\nstock
echo [%date% %time%] Running nstock mid... >> "%LOGFILE%"
python -u crawler_mid.py >> "%LOGFILE%" 2>&1

cd /d d:\StockPulse\stocks_news\yahoo
echo [%date% %time%] Running yahoo mid... >> "%LOGFILE%"
python -u crawler_mid.py >> "%LOGFILE%" 2>&1

cd /d d:\StockPulse\stocks_news\cnyes
echo [%date% %time%] Running cnyes mid... >> "%LOGFILE%"
python -u crawler_mid.py >> "%LOGFILE%" 2>&1

echo [%date% %time%] Daily (mid) crawlers done. >> "%LOGFILE%"

REM ============================================
REM  Cleanup old news (>90 days)
REM ============================================
cd /d d:\StockPulse\stocks_news
echo [%date% %time%] Cleaning up old news... >> "%LOGFILE%"
python -u cleanup_old_news.py >> "%LOGFILE%" 2>&1
echo [%date% %time%] Cleanup done. >> "%LOGFILE%"

REM ============================================
REM  Hot tier crawlers + content fetch + sentiment
REM  (this single fetch_content / analyze_sentiment pass also
REM   processes the mid-tier news crawled above, so we do NOT
REM   run those phases separately here anymore)
REM ============================================
call "d:\StockPulse\stocks_news\run_crawlers_hot.bat"

REM Restore sleep timeout (30 minutes)
powercfg /change standby-timeout-ac 30
