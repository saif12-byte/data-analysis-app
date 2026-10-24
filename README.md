# Data Analysis App

## Requirements

- Node.js 18 or newer
- Python 3.10 or newer

## First-time setup

From the project root in PowerShell:

```powershell
npm install
py -3 -m venv data
.\data\Scripts\python.exe -m pip install -r backend\requirements.txt
```

The `data` directory is the Python virtual environment. Python application code and
dependency declarations live in `backend/`; do not move source files into the
virtual environment because it is generated and can be recreated.

## Run the app

Open two terminals at the project root:

```powershell
npm run backend
```

```powershell
npm run dev
```

Open the local URL printed by Vite. During development, Vite forwards `/api`
requests to the Python API on `127.0.0.1:8000`. The backend health check is
available at `/api/health`.

CSV files are previewed in the browser and analysed by the Python API. Uploads
are limited to 25 MB and are processed in memory; the backend does not save
uploaded files to disk.

The Data validation tool supports CSV files with up to 150,000 data rows for
browser-based checks of missing values, duplicate rows, CSV row issues, and
mixed column types. Users can also configure custom per-column rules using
equals, does not equal, contains, does not contain, greater than, less than,
is empty, or is not empty conditions, with error, warning, or info severity. On
validation, the CSV and rules are sent to the FastAPI backend, which evaluates
each rule against every data row and returns rule counts, severity summaries,
and failing row details. After validation, users can download a CSV report with
the overall pass/fail status, custom rule outcomes, automatic check results,
and all detected issue details. The on-screen custom-rule failures show up to
the first 1,000 rows; summary counts and the downloadable issue report cover the
full dataset. Validation result tables display 10 entries per page, with page
navigation for columns, rules, and failing rows. Files are processed in memory
and are not saved; API uploads are limited to 25 MB.

After validation, users can download separate cleaned-data and issue-report
CSV files. The original upload is never overwritten. Error, warning, and info
groups are shown separately with sample spreadsheet row numbers; no groups are
selected for removal by default. Selecting a group removes all affected rows,
and the app previews how many unique rows will be removed. The issue report
lists every detected issue and whether its row was removed or kept. Use
"Upload corrected CSV" to validate a manually fixed file.
CSV parsing issues are reported separately and require correcting and
re-uploading the CSV because affected rows cannot be safely removed.

Error and Warning rules report rows that do not match their condition. Info
rules report rows that do match; these matching rows are informational, not
failures, and cannot be selected for removal.

## Production

Build the frontend with `npm run build` and serve the generated `dist/` directory
with a static web server. Configure that server to forward `/api` requests to
the Python API, or set `VITE_API_URL` to the deployed API base URL before
building. If the browser connects directly to a separately hosted API, set
`DATA_ANALYSIS_CORS_ORIGINS` on the API to a comma-separated list of the exact
frontend origins. Run the API with a production ASGI server command such as:

```powershell
.\data\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000
```
