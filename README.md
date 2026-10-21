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
are limited to 10 MB and are processed in memory; the backend does not save
uploaded files to disk.

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
