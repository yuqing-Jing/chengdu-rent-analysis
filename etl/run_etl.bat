@echo off
:: 1. 设置项目根目录的绝对路径
set PROJECT_DIR=C:\Users\JYQ\chengdu-rent-analysis

:: 2. 强制切换到项目根目录 (解决读取 config/config.yaml 相对路径报错的致命坑)
cd /d %PROJECT_DIR%

:: 3. 使用 Python 的绝对路径执行脚本，并将输出追加到日志文件
"C:\Users\JYQ\AppData\Local\Programs\Python\Python310\python.exe" etl\clean_data.py >> logs\etl.log 2>&1

:: 4. 记录执行完成时间
echo [%date% %time%] ETL 任务执行完毕 >> logs\etl.log
