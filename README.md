JobScraper-Benchmark — updated collection
What changed based on the previous run
- Removed Bayt from the India-focused source list. The prior run showed Bayt falling back to worldwide results for Indian city searches.
- Kept LinkedIn for city searches, but excluded it from remote-only searches because JobSpy reported that LinkedIn ignores is_remote.
- Added a stop condition for LinkedIn after a 429/rate-limit/block signal appears in JobSpy's scraper error or warning metadata.
- Kept the 72-hour posting window and 25 results per source/search. Do not increase volume until the smaller run is validated.
- Deduplication now normalizes URLs and aggregates matched_keywords, matched_locations, matched_search_types, and matched_run_ids.
- URL-less rows are retained separately rather than silently discarded.
- Added a data-quality report for missing title, company, location, URL, description, job type, experience range, and posting date.
- Added checkpointing and per-search status/error details.
Search volume
The configuration has 117 city searches (13 roles × 9 cities) and 8 remote searches, for 125 search combinations.

Each city search uses up to four sources. Each remote search uses three sources (Indeed, Glassdoor, Naukri). These are upper bounds, not guaranteed result counts.
Install and run
Use Python 3.10+ in a virtual environment:
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# macOS/Linux
source .venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt
python run_jobspy.py
If an older python-jobspy is installed, update it before the next test:
pip install --upgrade python-jobspy
pip show python-jobspy
Record the installed version when comparing runs. Naukri's previous missing-field validation errors may be caused by a scraper/parser schema change; this update logs those errors but does not invent missing values or pretend the parser is fixed.
Output files
Created under jobspy/:
- jobspy_all_jobs.json — deduplicated jobs with search provenance
- jobspy_all_jobs.csv — same records in CSV format
- jobspy_errors.json — source warnings/errors and search exceptions
- jobspy_quality_report.json — missing-field percentages and quality notes
- jobspy_run_summary.json — run timing, counts, per-search stats
- checkpoint.jsonl — raw records collected so far; useful if interrupted
Important limitations
1. A successful scrape does not prove a listing is genuine, current, or still accepting applications.
2. JobSpy's hours_old=72 is a source-side filter; verify date_posted in the original listing.
3. Date-only values do not reveal exact posting time.
4. A remote search does not prove India work eligibility. Verify country restrictions, payroll location, and work authorization from the job description.
5. Exact URL deduplication does not merge cross-platform duplicates. Use the existing comparison workflow for review-only fuzzy matching.
6. Description fetching remains off for broad searches. The next improvement should be a targeted detail-enrichment stage for shortlisted jobs, not enabling detail fetching across every search.
7. Do not use aggressive proxy rotation or repeated retries to bypass LinkedIn blocks. If LinkedIn is rate-limited, let the run continue with the other sources and use permitted access methods.
Recommended validation before daily automation
1. Run this once manually.
2. Check jobspy_run_summary.json and jobspy_quality_report.json.
3. Manually inspect at least 20 listings from each source, including their original URLs, location, date, and role relevance.
4. Check that remote jobs are genuinely open to candidates based in India.
5. Check that Naukri errors have reduced after updating python-jobspy.
6. Only then schedule a daily run. Start with once per day; do not run overlapping jobs.