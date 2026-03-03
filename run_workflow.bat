@echo off
echo ==========================================
echo  NSW Health RN Job Scraper - Full Workflow
echo ==========================================
echo.

REM Step 1: Scrape jobs (opens browser window)
echo [1/3] Running scraper...
python scrapefaster.py
if errorlevel 1 (
    echo ERROR: Scraper failed. Check the output above.
    pause
    exit /b 1
)

REM Step 2: Convert CSV to JSON for frontend
echo.
echo [2/3] Converting CSV to jobs.json...
python convert.py
if errorlevel 1 (
    echo ERROR: Conversion failed.
    pause
    exit /b 1
)

REM Step 3: Generate the Folium map
echo.
echo [3/3] Generating interactive map...
python processjobs.py
if errorlevel 1 (
    echo WARNING: Map generation failed. Check processjobs.py output.
)

REM Copy latest map to the expected location for the frontend
echo.
echo Copying latest map to output/job_map.html...
for /f "delims=" %%i in ('dir /b /od output\job_map_2*.html 2^>nul') do set LATEST=%%i
if defined LATEST (
    copy "output\%LATEST%" "output\job_map.html" >nul
    echo Copied %LATEST% to output/job_map.html
) else (
    echo No timestamped map found - output/job_map.html unchanged
)

echo.
echo ==========================================
echo  Done! Commit and push to deploy to Netlify:
echo  git add rn_jobs_with_incentives.csv jobs.json output/job_map.html
echo  git commit -m "Update job data"
echo  git push
echo ==========================================
pause
