@echo off
echo ========================================================
echo Executing Daily Cybersecurity Job Pipeline
echo ========================================================
call .\venv\Scripts\python.exe run_daily_pipeline.py --daily-run
echo ========================================================
echo Daily pipeline finished. Review jobspy/daily_run_report.md
echo ========================================================
pause

