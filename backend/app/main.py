from __future__ import annotations

import csv
import os
from pathlib import Path
import re
import tempfile
import time
from io import BytesIO

import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

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

COLORS = ["var(--primary)", "var(--secondary)", "var(--yellow)", "var(--muted)"]
VALUE_COLUMN_PRIORITY = re.compile(
    r"revenue|sales|amount|price|total|value|cost|quantity|qty", re.IGNORECASE
)
DATE_COLUMN_HINT = re.compile(r"date|time|month|day", re.IGNORECASE)

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
async def upload_csv(file: UploadFile = File(...)) -> dict:
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

    date_column = None
    date_values = None
    for column in columns:
        if DATE_COLUMN_HINT.search(column):
            parsed_dates = pd.to_datetime(dataframe[column], errors="coerce")
            if parsed_dates.notna().any():
                date_column = column
                date_values = parsed_dates
                break

    numeric_columns: list[tuple[str, pd.Series]] = []
    for column in columns:
        text_values = dataframe[column].astype("string").str.strip()
        normalized = text_values.str.replace(r"[,\s₹$€£%]", "", regex=True)
        values = pd.to_numeric(normalized, errors="coerce").dropna()
        if not values.empty:
            numeric_columns.append((column, values))

    numeric_columns.sort(
        key=lambda item: bool(VALUE_COLUMN_PRIORITY.search(item[0])),
        reverse=True,
    )
    primary_numeric = numeric_columns[0] if numeric_columns else None
    categorical_column = next(
        (
            column
            for column in columns
            if column != date_column
            and not any(numeric_column == column for numeric_column, _ in numeric_columns)
        ),
        None,
    )

    category_totals: dict[str, float] = {}
    if categorical_column:
        for index, value in dataframe[categorical_column].items():
            name = str(value).strip() if pd.notna(value) else ""
            name = name or "(blank)"
            amount = (
                pd.to_numeric(
                    str(dataframe.at[index, primary_numeric[0]])
                    .strip()
                    .replace(",", "")
                    .replace("₹", "")
                    .replace("$", "")
                    .replace("€", "")
                    .replace("£", "")
                    .replace("%", ""),
                    errors="coerce",
                )
                if primary_numeric
                else 1
            )
            if pd.notna(amount):
                category_totals[name] = category_totals.get(name, 0.0) + float(amount)

    top_categories = sorted(category_totals.items(), key=lambda item: item[1], reverse=True)[:6]
    category_total = sum(value for _, value in top_categories)
    categories = []
    assigned_share = 0
    for index, (name, value) in enumerate(top_categories):
        share = (
            100 - assigned_share
            if index == len(top_categories) - 1
            else round(value / category_total * 100)
            if category_total
            else 0
        )
        assigned_share += share
        categories.append({"name": name, "share": share, "color": COLORS[index % len(COLORS)]})
    if not categories:
        categories = [{"name": "All rows", "share": 100, "color": COLORS[0]}]

    rows_count = len(dataframe)
    stats = []
    if primary_numeric:
        numeric_name, values = primary_numeric
        total = float(values.sum())
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
                "label": f"Total {numeric_name}",
                "value": total,
                "format": "number",
                "detail": f"{len(values)} numeric values",
            },
            {
                "id": "average",
                "label": f"Average {numeric_name}",
                "value": float(values.mean()),
                "format": "number",
                "detail": "Per numeric value",
            },
            {
                "id": "maximum",
                "label": f"Highest {numeric_name}",
                "value": float(values.max()),
                "format": "number",
                "detail": "Largest value found",
            },
        ]
    else:
        stats = [
            {
                "id": "rows",
                "label": "Rows analysed",
                "value": rows_count,
                "format": "number",
                "detail": "Records in your file",
            },
            {
                "id": "columns",
                "label": "Columns",
                "value": len(columns),
                "format": "number",
                "detail": "Fields in your file",
            },
            {
                "id": "categories",
                "label": "Categories",
                "value": len(category_totals),
                "format": "number",
                "detail": categorical_column or "Distinct values",
            },
            {
                "id": "values",
                "label": "Data points",
                "value": rows_count * len(columns),
                "format": "number",
                "detail": "Values across all rows",
            },
        ]

    trend = _build_trend(dataframe, date_column, date_values, primary_numeric)
    filename = Path(file.filename or "upload.csv").name
    value_name = primary_numeric[0] if primary_numeric else "Rows"
    return {
        "subtitle": f"{filename} · {rows_count:,} rows",
        "stats": stats,
        "trend": trend,
        "trendTitle": f"Trend · {value_name}",
        "trendSubtitle": (
            f"Grouped by {'date' if date_column else 'row'} · "
            f"{primary_numeric[0] if primary_numeric else 'Record count'}"
        ),
        "trendValueLabel": value_name,
        "trendFormat": "number",
        "categories": categories,
        "categoryTitle": f"By {categorical_column}" if categorical_column else "Row distribution",
        "categorySubtitle": f"Share of {primary_numeric[0] if primary_numeric else 'records'}",
    }


def _build_trend(
    dataframe: pd.DataFrame,
    date_column: str | None,
    date_values: pd.Series | None,
    primary_numeric: tuple[str, pd.Series] | None,
) -> list[dict[str, int | float | str]]:
    if date_column and date_values is not None:
        trend_frame = pd.DataFrame({"date": date_values})
        if primary_numeric:
            column = primary_numeric[0]
            trend_frame["value"] = pd.to_numeric(
                dataframe[column]
                .astype("string")
                .str.strip()
                .str.replace(r"[,\s₹$€£%]", "", regex=True),
                errors="coerce",
            )
        else:
            trend_frame["value"] = 1
        trend_frame = trend_frame.dropna(subset=["date", "value"])
        if not trend_frame.empty:
            trend_frame["period"] = trend_frame["date"].dt.to_period("M")
            grouped = trend_frame.groupby("period", sort=True)["value"].sum()
            return [
                {"month": period.strftime("%b %y"), "revenue": float(value)}
                for period, value in grouped.items()
            ]

    row_count = len(dataframe)
    bucket_size = max(1, (row_count + 7) // 8)
    trend = []
    for start in range(0, row_count, bucket_size):
        group = dataframe.iloc[start : start + bucket_size]
        if primary_numeric:
            column = primary_numeric[0]
            values = pd.to_numeric(
                group[column]
                .astype("string")
                .str.strip()
                .str.replace(r"[,\s₹$€£%]", "", regex=True),
                errors="coerce",
            ).dropna()
            value = float(values.sum())
        else:
            value = int(len(group))
        end = start + len(group)
        label = f"Row {start + 1}" if row_count <= 8 else f"Rows {start + 1}–{end}"
        trend.append({"month": label, "revenue": value})
    return trend
