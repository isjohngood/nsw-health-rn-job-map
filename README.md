# NSW Health RN Job Map

Interactive map and statistics dashboard for Registered Nurse jobs advertised on [NSW Health Jobs](https://jobs.health.nsw.gov.au/).

## What it does

- Scrapes all RN job listings from NSW Health
- Extracts location, incentives, and closing date
- Displays jobs on an interactive map (Folium) with geocoded locations
- Shows statistics on the frontend: job counts by location, position type, incentives, and due date

Live site: https://nsw-health-rn-job-map.netlify.app

---

## Project structure

```
websiteIdea/
├── index.html              # Frontend dashboard (loads jobs.json)
├── jobs.json               # Job data for the frontend
├── all_locations.html      # Full location breakdown page
├── output/
│   └── job_map.html        # Interactive Folium map (linked from index.html)
│
├── scrapefaster.py         # Main scraper — Selenium + Chrome
├── processjobs.py          # Generates output/job_map.html from CSV
├── convert.py              # Converts CSV → jobs.json
│
├── rn_jobs_with_incentives.csv   # Scraped job data (source of truth)
├── requirements.txt        # Python dependencies
└── config.json             # Scraper settings
```

---

## Setup (Linux / WSL)

### 1. Install Chromium (required by scraper)

```bash
sudo apt update && sudo apt install -y chromium-browser
```

### 2. Create a Python virtual environment

```bash
python3 -m venv .venv-linux
source .venv-linux/bin/activate
pip install -r requirements.txt
```

### 3. Run the scraper

> **Important:** The NSW Health jobs site blocks headless browsers. The scraper must run with a visible Chrome window (i.e., on Windows, not WSL2 headless). Use the Windows Python environment.

Run the full workflow in one step:
```
run_workflow.bat
```

Or run individual steps:
```
python scrapefaster.py    # scrape jobs → rn_jobs_with_incentives.csv
python convert.py          # CSV → jobs.json (for frontend)
python processjobs.py      # jobs.json → output/job_map.html
```

The scraper opens a browser window, searches for "Registered Nurse" on the NSW Health site, and pages through all results. It saves results to `rn_jobs_with_incentives.csv`.

> **Settings in config.json:**
> - `FETCH_ALL_DUE_DATES` — fetch closing dates for all jobs (slower but more complete)
> - `REMOVE_UNLISTED_JOBS` — remove jobs no longer on the site (caution: loses history)
> - `ENABLE_ALERT_BEEP` — audible beep when new jobs are found

### 4. Convert CSV to JSON (for frontend)

```bash
python convert.py
```

This writes `jobs.json` which the frontend loads.

### 5. Generate the interactive map

```bash
python processjobs.py
```

This writes `output/job_map.html`.

---

## Deploying to Netlify

The site is a static frontend — deploy by pushing to the connected git repository. Netlify serves `index.html` with `jobs.json` and `output/job_map.html`.

Files that must be committed after each scrape:
- `jobs.json`
- `output/job_map.html`
- `rn_jobs_with_incentives.csv` (optional, for history)

---

## Windows setup (original)

The `.venv/` folder is a Windows Python virtual environment. On Windows, activate with:

```powershell
.venv\Scripts\activate
python scrapefaster.py
```
