@echo off
setlocal

REM 切到專案資料夾（確保相對路徑/檔案都正常）
cd /d D:\__mypostgres_test\python_desktop

REM 執行程式，輸出 log
"C:\Users\brian\AppData\Local\Python\pythoncore-3.14-64\python.exe" "D:\__mypostgres_test\python_desktop\yahoo_2330_news_to_pg_dt.py" >> "D:\__mypostgres_test\python_desktop\logs\run.log" 2>&1

endlocal
