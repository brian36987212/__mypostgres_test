@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

REM Define log path first
set LOGDIR=d:\StockPulse\stocks_news\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
for /f %%D in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set TODAY=%%D
set LOGFILE=%LOGDIR%\crawler_hot_%TODAY%.log

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

REM Load environment variables from .env
for /f "usebackq eol=# tokens=1,* delims==" %%A in ("d:\StockPulse\.env") do (
    set "%%A=%%B"
)

REM ============================================
REM  Hot tier crawlers
REM ============================================

REM Clear hot progress files
del /q "d:\StockPulse\stocks_news\nstock\progress_nstock_hot.txt" 2>nul
del /q "d:\StockPulse\stocks_news\yahoo\progress_hot.txt" 2>nul
del /q "d:\StockPulse\stocks_news\cnyes\progress_cnyes_hot.txt" 2>nul

echo ============================================ >> "%LOGFILE%"
echo [%date% %time%] Hot crawlers starting >> "%LOGFILE%"
echo ============================================ >> "%LOGFILE%"

cd /d d:\StockPulse\stocks_news\nstock
echo [%date% %time%] Running nstock hot... >> "%LOGFILE%"
python -u crawler_hot.py >> "%LOGFILE%" 2>&1

cd /d d:\StockPulse\stocks_news\yahoo
echo [%date% %time%] Running yahoo hot... >> "%LOGFILE%"
python -u crawler_hot.py >> "%LOGFILE%" 2>&1

cd /d d:\StockPulse\stocks_news\cnyes
echo [%date% %time%] Running cnyes hot... >> "%LOGFILE%"
python -u crawler_hot.py >> "%LOGFILE%" 2>&1

echo [%date% %time%] Hot crawlers done. >> "%LOGFILE%"

REM ============================================
REM  Phase 2: Fetch article content
REM ============================================
echo [%date% %time%] Fetching content... >> "%LOGFILE%"

cd /d d:\StockPulse\stocks_news\nstock
echo [%date% %time%] Running nstock fetch_content... >> "%LOGFILE%"
python -u fetch_content.py >> "%LOGFILE%" 2>&1

cd /d d:\StockPulse\stocks_news\yahoo
echo [%date% %time%] Running yahoo fetch_content... >> "%LOGFILE%"
python -u fetch_content.py >> "%LOGFILE%" 2>&1

cd /d d:\StockPulse\stocks_news\cnyes
echo [%date% %time%] Running cnyes fetch_content... >> "%LOGFILE%"
python -u fetch_content.py >> "%LOGFILE%" 2>&1

REM ============================================
REM  Phase 3: Sentiment analysis
REM ============================================
echo [%date% %time%] Analyzing sentiment... >> "%LOGFILE%"

cd /d d:\StockPulse\stocks_news\nstock
echo [%date% %time%] Running nstock analyze_sentiment... >> "%LOGFILE%"
python -u analyze_sentiment.py >> "%LOGFILE%" 2>&1

cd /d d:\StockPulse\stocks_news\yahoo
echo [%date% %time%] Running yahoo analyze_sentiment... >> "%LOGFILE%"
python -u analyze_sentiment.py >> "%LOGFILE%" 2>&1

cd /d d:\StockPulse\stocks_news\cnyes
echo [%date% %time%] Running cnyes analyze_sentiment... >> "%LOGFILE%"
python -u analyze_sentiment.py >> "%LOGFILE%" 2>&1

echo [%date% %time%] Hot pipeline complete. >> "%LOGFILE%"
