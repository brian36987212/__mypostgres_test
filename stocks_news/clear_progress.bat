@echo off
REM ============================================
REM  清除所有 progress files，讓爬蟲重新抓全部股票
REM  搭配排程使用，每次跑之前先清 progress
REM ============================================
echo [%date% %time%] Clearing progress files...

del /q "d:\__mypostgres_test\python_desktop\stocks_news\nstock\progress_nstock_*.txt" 2>nul
del /q "d:\__mypostgres_test\python_desktop\stocks_news\yahoo\progress_*.txt" 2>nul
del /q "d:\__mypostgres_test\python_desktop\stocks_news\cnyes\progress_cnyes_*.txt" 2>nul

echo [%date% %time%] Progress files cleared.
