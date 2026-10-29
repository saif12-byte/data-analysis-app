from __future__ import annotations

import csv
import gzip
import os
from pathlib import Path
import re
import tempfile
import time
from io import BytesIO
from hashlib import sha256

from dotenv import load_dotenv
import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from .services.ai.gemini import generate_ai_insights
from .services.insights import (
    answer_dataset_question,
    build_insight_pack,
    get_dataset_csv,
    get_insight_pack,
    store_insight_pack,
)
from .validation import (
    MAX_FILE_SIZE,
    _log_validation_timing,
    build_validation_report,
    evaluate_dataset,
    issue_group_page,
    parse_selected_groups,
    read_validation_input,
    selected_row_indexes,
)

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

COLORS = ["var(--primary)", "var(--secondary)", "var(--yellow)", "var(--muted)"]
VALUE_COLUMN_PRIORITY = re.compile(
    r"revenue|sales|amount|price|total|value|cost|quantity|qty", re.IGNORECASE
)
DATE_COLUMN_HINT = re.compile(r"date|time|month|day", re.IGNORECASE)
AGGREGATIONS = {"sum", "mean", "median", "min", "max", "count"}
CHART_TYPES = {"line", "area", "bar"}


class DatasetQuestionRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)


def _numeric_values(dataframe: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(
        dataframe[column]
        .astype("string")
        .str.strip()
        .str.replace(r"[,\s₹$€£%]", "", regex=True),
        errors="coerce",
    )


def _aggregate(values: pd.Series, aggregation: str) -> int | float:
    if aggregation == "count":
        return int(values.count())
    valid_values = values.dropna()
    if valid_values.empty:
        return 0
    result = getattr(valid_values, aggregation)()
    return float(result)


def _resolve_column(
    requested: str, columns: list[str], fallback: str | None, label: str
) -> str | None:
    if requested == "auto":
        return fallback
    if requested == "none":
        return None
    if requested not in columns:
        raise HTTPException(status_code=400, detail=f"Unknown {label} column.")
    return requested

app = FastAPI(title="Data Analysis API", version="1.0.0")
allowed_origins = [
    origin.strip()
    for origin in os.getenv(
        "DATA_ANALYSIS_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:4173",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/datasets/{dataset_id}/insights")
def get_dataset_insights(dataset_id: str) -> dict:
    record = get_insight_pack(dataset_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Dataset insights were not found. Upload the CSV again.")
    _, pack = record
    feature_enabled = os.getenv("AI_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    key_configured = bool(os.getenv("GEMINI_API_KEY", "").strip())
    return {
        "dataset_id": dataset_id,
        "pack": pack,
        "ai_feature_enabled": feature_enabled,
        "ai_key_configured": key_configured,
        "ai_enabled": feature_enabled and key_configured,
    }


@app.post("/api/datasets/{dataset_id}/ai/insights")
async def generate_dataset_ai_insights(dataset_id: str) -> dict:
    record = get_insight_pack(dataset_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Dataset insights were not found. Upload the CSV again.")
    data_hash, pack = record
    return await generate_ai_insights(dataset_id, data_hash, pack)


@app.post("/api/datasets/{dataset_id}/questions")
def ask_dataset_question(
    dataset_id: str, request: DatasetQuestionRequest
) -> dict:
    if get_insight_pack(dataset_id) is None:
        raise HTTPException(
            status_code=404,
            detail="Dataset was not found. Upload and analyse the CSV again.",
        )
    compressed_csv = get_dataset_csv(dataset_id)
    if compressed_csv is None:
        raise HTTPException(
            status_code=410,
            detail="The dataset is no longer available for questions. Upload it again.",
        )
    try:
        dataframe = pd.read_csv(BytesIO(gzip.decompress(compressed_csv)), encoding="utf-8-sig")
        dataframe = dataframe.dropna(how="all")
        result = answer_dataset_question(dataframe, request.question)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (OSError, EOFError, UnicodeDecodeError, pd.errors.ParserError) as error:
        raise HTTPException(
            status_code=500,
            detail="Could not read the temporary dataset for this question.",
        ) from error
    return result


def _safe_csv_download_stem(filename: str | None) -> str:
    stem = Path(filename or "data.csv").stem
    safe_stem = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", stem).strip(" .")
    return safe_stem or "data"


def _csv_file_response(
    dataframe: pd.DataFrame, filename: str, stage: str, started_at: float
) -> FileResponse:
    output_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8-sig",
            newline="",
            suffix=".csv",
            delete=False,
        ) as output:
            output_path = output.name
            dataframe.to_csv(output, index=False, quoting=csv.QUOTE_MINIMAL)
    except Exception:
        if output_path and os.path.exists(output_path):
            os.unlink(output_path)
        raise
    _log_validation_timing(stage, started_at, rows=len(dataframe))
    return FileResponse(
        output_path,
        media_type="text/csv; charset=utf-8",
        filename=filename,
        background=BackgroundTask(os.unlink, output_path),
    )


@app.post("/api/validate")
async def validate_csv(
    file: UploadFile = File(...),
    rules: str = Form(...),
    csv_issues: str = Form("[]"),
) -> dict:
    _, parsed_rules, dataframe, parsed_csv_issues = await read_validation_input(
        file, rules, csv_issues
    )
    result = evaluate_dataset(dataframe, parsed_rules, parsed_csv_issues)
    response_started_at = time.perf_counter()
    automatic = {
        key: value
        for key, value in result.automatic.items()
        if key != "csvIssues"
    }
    response = {
        "rows": result.automatic["rows"],
        "columns": result.automatic["columns"],
        "issueGroups": result.issue_groups,
        "ruleResults": result.rule_results,
        "summary": result.summary,
        "automatic": automatic,
    }
    _log_validation_timing(
        "api_response", response_started_at, groups=len(result.issue_groups)
    )
    return response


@app.post("/api/validate/issue-groups/{group_id}/rows")
async def get_issue_group_rows(
    group_id: str,
    file: UploadFile = File(...),
    rules: str = Form(...),
    csv_issues: str = Form("[]"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
) -> dict:
    _, parsed_rules, dataframe, parsed_csv_issues = await read_validation_input(
        file, rules, csv_issues
    )
    return issue_group_page(
        dataframe, parsed_rules, parsed_csv_issues, group_id, page, page_size
    )


@app.post("/api/clean-preview")
async def preview_cleaned_csv(
    file: UploadFile = File(...),
    rules: str = Form(...),
    selected_groups: str = Form("[]"),
    csv_issues: str = Form("[]"),
) -> dict:
    _, parsed_rules, dataframe, parsed_csv_issues = await read_validation_input(
        file, rules, csv_issues
    )
    result = evaluate_dataset(dataframe, parsed_rules, parsed_csv_issues)
    selected_group_ids = parse_selected_groups(selected_groups)
    removed_rows = selected_row_indexes(result, selected_group_ids)
    return {
        "rows": len(dataframe),
        "removedRows": len(removed_rows),
        "remainingRows": len(dataframe) - len(removed_rows),
    }


@app.post("/api/cleaned-data")
async def download_cleaned_csv(
    file: UploadFile = File(...),
    rules: str = Form(...),
    selected_groups: str = Form("[]"),
    csv_issues: str = Form("[]"),
) -> FileResponse:
    _, parsed_rules, dataframe, parsed_csv_issues = await read_validation_input(
        file, rules, csv_issues
    )
    result = evaluate_dataset(dataframe, parsed_rules, parsed_csv_issues)
    selected_group_ids = parse_selected_groups(selected_groups)
    removed_rows = selected_row_indexes(result, selected_group_ids)
    generation_started_at = time.perf_counter()
    cleaned = dataframe.loc[~dataframe.index.isin(removed_rows)]
    filename = f"{_safe_csv_download_stem(file.filename)}-cleaned.csv"
    return _csv_file_response(
        cleaned, filename, "cleaned_csv_generation", generation_started_at
    )


@app.post("/api/validation-report")
async def download_validation_report(
    file: UploadFile = File(...),
    rules: str = Form(...),
    selected_groups: str = Form("[]"),
    csv_issues: str = Form("[]"),
) -> FileResponse:
    _, parsed_rules, dataframe, parsed_csv_issues = await read_validation_input(
        file, rules, csv_issues
    )
    result = evaluate_dataset(dataframe, parsed_rules, parsed_csv_issues)
    selected_group_ids = parse_selected_groups(selected_groups)
    removed_rows = selected_row_indexes(result, selected_group_ids)
    generation_started_at = time.perf_counter()
    report = build_validation_report(
        dataframe, result, selected_group_ids, removed_rows
    )
    filename = f"{_safe_csv_download_stem(file.filename)}-validation-report.csv"
    return _csv_file_response(
        report, filename, "issue_report_generation", generation_started_at
    )


@app.post("/api/upload")
async def upload_csv(
    file: UploadFile = File(...),
    measure_column: str = Form("auto"),
    date_column: str = Form("auto"),
    group_column: str = Form("auto"),
    aggregation: str = Form("sum"),
    trend_chart: str = Form("area"),
    category_chart: str = Form("bar"),
) -> dict:
    if Path(file.filename or "").suffix.lower() != ".csv":
        raise HTTPException(status_code=415, detail="Upload a .csv file.")

    contents = await file.read(MAX_FILE_SIZE + 1)
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="CSV files must be 25 MB or smaller.")
    if not contents:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    try:
        dataframe = pd.read_csv(BytesIO(contents), encoding="utf-8-sig")
    except (UnicodeDecodeError, pd.errors.ParserError, ValueError) as error:
        raise HTTPException(
            status_code=400,
            detail="Could not read this CSV. Check that it is valid UTF-8 CSV data.",
        ) from error

    dataframe = dataframe.dropna(how="all")
    columns = [str(column) for column in dataframe.columns]
    if not columns:
        raise HTTPException(status_code=400, detail="No columns found in this CSV.")
    if dataframe.empty:
        raise HTTPException(status_code=400, detail="This CSV has column names but no data rows.")

    numeric_columns: list[tuple[str, pd.Series]] = []
    for column in columns:
        values = _numeric_values(dataframe, column).dropna()
        if not values.empty:
            numeric_columns.append((column, values))

    numeric_columns.sort(
        key=lambda item: bool(VALUE_COLUMN_PRIORITY.search(item[0])),
        reverse=True,
    )
    auto_numeric = numeric_columns[0] if numeric_columns else None
    primary_numeric = _resolve_column(
        measure_column,
        columns,
        auto_numeric[0] if auto_numeric else None,
        "measure",
    )
    if primary_numeric:
        numeric_values = _numeric_values(dataframe, primary_numeric)
        if not numeric_values.notna().any():
            raise HTTPException(
                status_code=400,
                detail=f"The selected measure column '{primary_numeric}' has no numeric values.",
            )
    else:
        numeric_values = pd.Series(1, index=dataframe.index, dtype="int64")

    auto_date_column = None
    for column in columns:
        if DATE_COLUMN_HINT.search(column):
            parsed_dates = pd.to_datetime(dataframe[column], errors="coerce")
            if parsed_dates.notna().any():
                auto_date_column = column
                break
    selected_date_column = _resolve_column(
        date_column, columns, auto_date_column, "date"
    )
    selected_date_values = (
        pd.to_datetime(dataframe[selected_date_column], errors="coerce")
        if selected_date_column
        else None
    )
    if selected_date_column and not selected_date_values.notna().any():
        raise HTTPException(
            status_code=400,
            detail=f"The selected date column '{selected_date_column}' has no valid dates.",
        )

    auto_group_column = next(
        (
            column
            for column in columns
            if column != auto_date_column
            and column != (auto_numeric[0] if auto_numeric else None)
            and column not in [numeric_column for numeric_column, _ in numeric_columns]
        ),
        None,
    )
    categorical_column = _resolve_column(
        group_column, columns, auto_group_column, "group"
    )
    if aggregation not in AGGREGATIONS:
        raise HTTPException(status_code=400, detail="Choose a supported aggregation.")
    if trend_chart not in CHART_TYPES:
        raise HTTPException(status_code=400, detail="Choose a supported trend chart.")
    if category_chart not in {"bar", "pie"}:
        raise HTTPException(status_code=400, detail="Choose a supported group chart.")

    category_totals: dict[str, int | float] = {}
    if categorical_column:
        group_values = dataframe[categorical_column].astype("string").fillna("(blank)")
        grouped_values = pd.DataFrame(
            {"group": group_values, "value": numeric_values}
        ).groupby("group", dropna=False)["value"]
        category_totals = {
            str(name): _aggregate(values, aggregation)
            for name, values in grouped_values
        }
    else:
        category_totals = {
            "All rows": _aggregate(numeric_values, aggregation)
        }

    top_categories = sorted(
        category_totals.items(), key=lambda item: abs(item[1]), reverse=True
    )[:12]
    category_total = sum(abs(value) for _, value in top_categories)
    categories = []
    for index, (name, value) in enumerate(top_categories):
        categories.append(
            {
                "name": name,
                "value": value,
                "share": round(abs(value) / category_total * 100) if category_total else 0,
                "color": COLORS[index % len(COLORS)],
            }
        )
    if not categories:
        categories = [{"name": "All rows", "value": 0, "share": 100, "color": COLORS[0]}]

    rows_count = len(dataframe)
    measure_name = primary_numeric or "Rows"
    valid_numeric_values = numeric_values.dropna()
    stats = [
        {
            "id": "rows",
            "label": "Rows analysed",
            "value": rows_count,
            "format": "number",
            "detail": "Records in your file",
        },
        {
            "id": "total",
            "label": f"Total {measure_name}",
            "value": float(valid_numeric_values.sum()),
            "format": "number",
            "detail": f"{len(valid_numeric_values)} numeric values",
        },
        {
            "id": "average",
            "label": f"Average {measure_name}",
            "value": float(valid_numeric_values.mean()) if not valid_numeric_values.empty else 0,
            "format": "number",
            "detail": "Per numeric value",
        },
        {
            "id": "maximum",
            "label": f"Highest {measure_name}",
            "value": float(valid_numeric_values.max()) if not valid_numeric_values.empty else 0,
            "format": "number",
            "detail": "Largest value found",
        },
    ]

    trend = _build_trend(
        dataframe,
        selected_date_column,
        selected_date_values,
        numeric_values,
        aggregation,
    )
    insight_pack = build_insight_pack(dataframe)
    dataset_id = store_insight_pack(
        sha256(contents).hexdigest(), insight_pack, contents
    )
    filename = Path(file.filename or "upload.csv").name
    value_name = primary_numeric or "Rows"
    return {
        "dataset_id": dataset_id,
        "subtitle": f"{filename} · {rows_count:,} rows",
        "stats": stats,
        "trend": trend,
        "trendTitle": f"{aggregation.title()} · {value_name} over time",
        "trendSubtitle": (
            f"Grouped by {selected_date_column or 'row order'} · {value_name}"
        ),
        "trendValueLabel": value_name,
        "trendFormat": "number",
        "trendChart": trend_chart,
        "categories": categories,
        "categoryTitle": f"{aggregation.title()} by {categorical_column or 'group'}",
        "categorySubtitle": f"{value_name} · top {len(categories)} groups",
        "categoryChart": category_chart,
    }


def _build_trend(
    dataframe: pd.DataFrame,
    date_column: str | None,
    date_values: pd.Series | None,
    numeric_values: pd.Series,
    aggregation: str,
) -> list[dict[str, int | float | str]]:
    if date_column and date_values is not None:
        trend_frame = pd.DataFrame({"date": date_values, "value": numeric_values})
        trend_frame = trend_frame.dropna(subset=["date", "value"])
        if not trend_frame.empty:
            trend_frame["period"] = trend_frame["date"].dt.to_period("M")
            grouped = trend_frame.groupby("period", sort=True)["value"].agg(aggregation)
            return [
                {"month": period.strftime("%b %y"), "value": float(value)}
                for period, value in grouped.items()
            ]

    row_count = len(dataframe)
    bucket_size = max(1, (row_count + 7) // 8)
    trend = []
    for start in range(0, row_count, bucket_size):
        group_values = numeric_values.iloc[start : start + bucket_size]
        end = start + len(group_values)
        label = f"Row {start + 1}" if row_count <= 8 else f"Rows {start + 1}–{end}"
        trend.append({"month": label, "value": _aggregate(group_values, aggregation)})
    return trend
