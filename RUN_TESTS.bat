@echo off
echo ========================================================
echo Running JobScraper-Benchmark Automated Test Suite
echo ========================================================
call .\venv\Scripts\python.exe -m unittest discover tests -v
echo ========================================================
echo Test suite finished.
echo ========================================================
pause

