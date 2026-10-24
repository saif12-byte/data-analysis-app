from __future__ import annotations

import csv
import json
import math
import os
from pathlib import Path
import re
from collections import Counter
from io import BytesIO, StringIO

import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

MAX_FILE_SIZE = 25 * 1024 * 1024
MAX_REPORTED_FAILED_ROWS = 1000
MAX_ISSUE_SAMPLES = 5
VALIDATION_CONDITIONS = {
    "equals",
    "not_equals",
    "contains",
    "not_contains",
    "greater_than",
    "less_than",
    "is_empty",
    "is_not_empty",
}
VALIDATION_SEVERITIES = {"error", "warning", "info"}
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


def _validate_rule_payload(rules: list) -> None:
    if not isinstance(rules, list) or not rules:
        raise HTTPException(status_code=400, detail="Add at least one validation rule.")

    seen_rule_ids: set[str] = set()
    for index, rule in enumerate(rules, start=1):
        if not isinstance(rule, dict):
            raise HTTPException(status_code=400, detail=f"Rule {index} is invalid.")
        rule_id = rule.get("id", str(index))
        if not isinstance(rule_id, str) or not rule_id or rule_id in seen_rule_ids:
            raise HTTPException(status_code=400, detail=f"Rule {index} needs a unique ID.")
        seen_rule_ids.add(rule_id)
        if not isinstance(rule.get("column"), str) or not rule["column"].strip():
            raise HTTPException(status_code=400, detail=f"Choose a column for rule {index}.")
        condition = rule.get("condition")
        if not isinstance(condition, str) or condition not in VALIDATION_CONDITIONS:
            raise HTTPException(status_code=400, detail=f"Choose a supported condition for rule {index}.")
        severity = rule.get("severity")
        if not isinstance(severity, str) or severity not in VALIDATION_SEVERITIES:
            raise HTTPException(status_code=400, detail=f"Choose a supported severity for rule {index}.")
        if condition not in {"is_empty", "is_not_empty"} and not isinstance(
            rule.get("value"), str
        ):
            raise HTTPException(status_code=400, detail=f"Enter a value for rule {index}.")
        if condition in {"greater_than", "less_than"}:
            try:
                numeric_value = float(rule["value"])
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail=f"Rule {index} needs a numeric value for this condition.",
                ) from None
            if not math.isfinite(numeric_value):
                raise HTTPException(
                    status_code=400,
                    detail=f"Rule {index} needs a finite numeric value for this condition.",
                )


def _rule_matches(value: object, condition: str, expected: str) -> bool:
    actual = "" if pd.isna(value) else str(value).strip()
    if condition == "is_empty":
        return not actual
    if condition == "is_not_empty":
        return bool(actual)
    if condition == "equals":
        return actual.casefold() == expected.strip().casefold()
    if condition == "not_equals":
        return actual.casefold() != expected.strip().casefold()
    if condition == "contains":
        return expected.casefold() in actual.casefold()
    if condition == "not_contains":
        return expected.casefold() not in actual.casefold()

    try:
        numeric_value = float(actual.replace(",", ""))
    except ValueError:
        return False
    numeric_expected = float(expected)
    if not math.isfinite(numeric_value):
        return False
    return numeric_value > numeric_expected if condition == "greater_than" else numeric_value < numeric_expected


def _get_validation_type(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"(?i:true|false)", text):
        return "Boolean"
    if re.fullmatch(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[T ].*)?", text) or re.fullmatch(
        r"\d{1,2}[-/]\d{1,2}[-/]\d{2,4}", text
    ):
        return "Date" if not pd.isna(pd.to_datetime(text, errors="coerce")) else "Text"
    numeric = re.sub(r"[,\s₹$€£%]", "", text)
    if numeric:
        try:
            if math.isfinite(float(numeric)):
                return "Number"
        except ValueError:
            pass
    return "Text"


def _evaluate_issues(
    dataframe: pd.DataFrame, rules: list, csv_issues: list[dict]
) -> tuple[list[dict], list[dict]]:
    groups: dict[str, dict] = {}
    issues: list[dict] = []

    def add_issue(
        group_id: str,
        label: str,
        severity: str,
        row_number: int,
        column: str,
        description: str,
        actual: object = "",
        expected: object = "",
        rule_id: str = "",
    ) -> None:
        group = groups.setdefault(
            group_id,
            {
                "id": group_id,
                "label": label,
                "severity": severity,
                "canRemove": severity != "info" and group_id != "automatic:csv_rows",
                "affectedRows": set(),
                "issueCount": 0,
                "sampleRows": [],
            },
        )
        if row_number is not None:
            group["affectedRows"].add(row_number)
        group["issueCount"] += 1
        if row_number is not None and len(group["sampleRows"]) < MAX_ISSUE_SAMPLES and row_number not in group["sampleRows"]:
            group["sampleRows"].append(row_number)
        issues.append({
            "groupId": group_id,
            "rowNumber": row_number,
            "column": column,
            "description": description,
            "actual": "" if pd.isna(actual) else str(actual),
            "expected": "" if pd.isna(expected) else str(expected),
            "severity": severity,
            "ruleId": rule_id,
        })

    type_counts = {column: Counter() for column in dataframe.columns}
    row_types: list[dict[str, str | None]] = []
    seen_rows: set[str] = set()
    for row_number, (_, row) in enumerate(dataframe.iterrows(), start=2):
        values = {str(column): row[column] for column in dataframe.columns}
        typed = {column: _get_validation_type(value) for column, value in values.items()}
        row_types.append(typed)
        for column, value_type in typed.items():
            if value_type:
                type_counts[column][value_type] += 1

        signature = json.dumps([str(value).strip() for value in values.values()], ensure_ascii=False)
        if signature in seen_rows:
            add_issue(
                "automatic:duplicates",
                "Duplicate rows",
                "warning",
                row_number,
                "",
                "Duplicate of an earlier row",
            )
        else:
            seen_rows.add(signature)

        for rule_index, rule in enumerate(rules):
            actual = values[rule["column"]]
            expected = rule.get("value", "")
            matches = _rule_matches(actual, rule["condition"], expected)
            if (rule["severity"] == "info" and not matches) or (
                rule["severity"] != "info" and matches
            ):
                continue
            rule_id = str(rule.get("id", rule_index + 1))
            add_issue(
                f"custom:{rule_id}",
                f'{rule["column"]} {rule["condition"]} {expected}'.strip(),
                rule["severity"],
                row_number,
                rule["column"],
                (
                    f'{rule["column"]} matches {rule["condition"]}'
                    if rule["severity"] == "info"
                    else f'{rule["column"]} does not satisfy {rule["condition"]}'
                ) + (f" {expected}" if expected else ""),
                actual,
                expected,
                rule_id,
            )

        for column, value in values.items():
            if not str(value).strip():
                add_issue(
                    "automatic:missing",
                    "Missing values",
                    "error",
                    row_number,
                    column,
                    "Missing value",
                    value,
                )

    expected_types = {
        column: (counts.most_common(1)[0][0] if counts else None)
        for column, counts in type_counts.items()
    }
    for row_offset, typed in enumerate(row_types):
        row_number = row_offset + 2
        for column, value_type in typed.items():
            expected_type = expected_types[column]
            if value_type and expected_type and value_type != expected_type:
                actual = dataframe.iloc[row_offset][column]
                add_issue(
                    "automatic:mixed_types",
                    "Mixed column types",
                    "warning",
                    row_number,
                    column,
                    f"Expected {expected_type}, found {value_type}",
                    actual,
                    expected_type,
                )

    for issue in csv_issues:
        add_issue(
            "automatic:csv_rows",
            "CSV row issues",
            "error",
            issue["rowNumber"],
            "",
            f'{issue["code"]}: {issue["description"]}',
        )

    result_groups = [
        {
            **group,
            "affectedRows": len(group["affectedRows"]),
            "sampleRows": sorted(group["sampleRows"]),
        }
        for group in groups.values()
    ]
    return result_groups, issues


async def _read_validation_input(
    file: UploadFile, rules: str, csv_issues: str
) -> tuple[bytes, list, pd.DataFrame, list[dict]]:
    if Path(file.filename or "").suffix.lower() != ".csv":
        raise HTTPException(status_code=415, detail="Upload a .csv file.")

    contents = await file.read(MAX_FILE_SIZE + 1)
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="CSV files must be 25 MB or smaller.")
    if not contents:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    try:
        parsed_rules = json.loads(rules)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=400, detail="Validation rules must be valid JSON.") from error
    _validate_rule_payload(parsed_rules)

    try:
        parsed_csv_issues = json.loads(csv_issues)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=400, detail="CSV issues must be valid JSON.") from error
    if not isinstance(parsed_csv_issues, list) or any(
        not isinstance(issue, dict)
        or (
            issue.get("rowNumber") is not None
            and (not isinstance(issue["rowNumber"], int) or issue["rowNumber"] < 2)
        )
        or not isinstance(issue.get("code"), str)
        or not isinstance(issue.get("description"), str)
        for issue in parsed_csv_issues
    ):
        raise HTTPException(status_code=400, detail="CSV issue details are invalid.")

    try:
        dataframe = pd.read_csv(
            BytesIO(contents),
            encoding="utf-8-sig",
            dtype=str,
            keep_default_na=False,
            skip_blank_lines=False,
        )
    except (UnicodeDecodeError, pd.errors.ParserError, ValueError) as error:
        raise HTTPException(
            status_code=400,
            detail="Could not read this CSV. Check that it is valid UTF-8 CSV data.",
        ) from error

    dataframe = dataframe.fillna("")
    if dataframe.empty or len(dataframe.columns) == 0:
        raise HTTPException(status_code=400, detail="This CSV has column names but no data rows.")

    for index, rule in enumerate(parsed_rules, start=1):
        if rule["column"] not in dataframe.columns:
            raise HTTPException(
                status_code=400,
                detail=f'Rule {index} refers to missing column "{rule["column"]}".',
            )

    return contents, parsed_rules, dataframe, parsed_csv_issues


def _selected_row_indexes(
    groups: list[dict], issues: list[dict], selected_group_ids: list[str]
) -> set[int]:
    available_groups = {group["id"] for group in groups}
    removable_groups = {group["id"] for group in groups if group["canRemove"]}
    if any(
        group_id not in available_groups or group_id not in removable_groups
        for group_id in selected_group_ids
    ):
        raise HTTPException(status_code=400, detail="A selected issue group is not available.")
    selected = set(selected_group_ids)
    return {
        issue["rowNumber"] - 2
        for issue in issues
        if issue["groupId"] in selected and issue["rowNumber"] is not None
    }


def _safe_csv_download_stem(filename: str | None) -> str:
    stem = Path(filename or "data.csv").stem
    safe_stem = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", stem).strip(" .")
    return safe_stem or "data"


@app.post("/api/validate")
async def validate_csv(
    file: UploadFile = File(...),
    rules: str = Form(...),
    csv_issues: str = Form("[]"),
) -> dict:
    _, parsed_rules, dataframe, parsed_csv_issues = await _read_validation_input(file, rules, csv_issues)
    issue_groups, issues = _evaluate_issues(dataframe, parsed_rules, parsed_csv_issues)

    rule_results = [
        {
            "id": rule.get("id", str(index)),
            "column": rule["column"],
            "condition": rule["condition"],
            "value": rule.get("value", ""),
            "severity": rule["severity"],
            "failedRows": 0,
            "matchingRows": 0,
        }
        for index, rule in enumerate(parsed_rules, start=1)
    ]
    failures_by_row: dict[int, list[dict]] = {}
    error_row_numbers = set()
    warning_row_numbers = set()

    rule_indexes = {str(rule.get("id", index + 1)): index for index, rule in enumerate(parsed_rules)}
    for issue in issues:
        if issue["groupId"].startswith("custom:"):
            rule_result = rule_results[rule_indexes[issue["ruleId"]]]
            if issue["severity"] == "info":
                rule_result["matchingRows"] += 1
                continue
            rule_result["failedRows"] += 1
            failures_by_row.setdefault(issue["rowNumber"], []).append({
                "ruleId": issue["ruleId"],
                "column": issue["column"],
                "condition": parsed_rules[rule_indexes[issue["ruleId"]]]["condition"],
                "expected": issue["expected"],
                "actual": issue["actual"],
                "severity": issue["severity"],
            })
            if issue["severity"] == "error":
                error_row_numbers.add(issue["rowNumber"])
            else:
                warning_row_numbers.add(issue["rowNumber"])

    failed_rows = [
        {"rowNumber": row_number, "reasons": reasons}
        for row_number, reasons in list(sorted(failures_by_row.items()))[:MAX_REPORTED_FAILED_ROWS]
    ]

    failed_row_count = len(error_row_numbers | warning_row_numbers)
    info_row_numbers = {
        issue["rowNumber"]
        for issue in issues
        if issue["severity"] == "info" and issue["rowNumber"] is not None
    }
    return {
        "rows": len(dataframe),
        "columns": len(dataframe.columns),
        "issueGroups": issue_groups,
        "ruleResults": rule_results,
        "failedRows": failed_rows,
        "failedRowsTruncated": failed_row_count > len(failed_rows),
        "summary": {
            "passedRows": len(dataframe) - failed_row_count,
            "failedRows": failed_row_count,
            "errorRows": len(error_row_numbers),
            "warningRows": len(warning_row_numbers),
            "infoRows": len(info_row_numbers),
        },
    }


@app.post("/api/clean-preview")
async def preview_cleaned_csv(
    file: UploadFile = File(...),
    rules: str = Form(...),
    selected_groups: str = Form("[]"),
    csv_issues: str = Form("[]"),
) -> dict:
    _, parsed_rules, dataframe, parsed_csv_issues = await _read_validation_input(file, rules, csv_issues)
    try:
        selected_group_ids = json.loads(selected_groups)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=400, detail="Selected issue groups must be valid JSON.") from error
    if not isinstance(selected_group_ids, list) or not all(
        isinstance(group_id, str) for group_id in selected_group_ids
    ):
        raise HTTPException(status_code=400, detail="Selected issue groups must be a list of IDs.")

    groups, issues = _evaluate_issues(dataframe, parsed_rules, parsed_csv_issues)
    removed_rows = _selected_row_indexes(groups, issues, selected_group_ids)
    return {"rows": len(dataframe), "removedRows": len(removed_rows), "remainingRows": len(dataframe) - len(removed_rows)}


@app.post("/api/cleaned-data")
async def download_cleaned_csv(
    file: UploadFile = File(...),
    rules: str = Form(...),
    selected_groups: str = Form("[]"),
    csv_issues: str = Form("[]"),
) -> Response:
    _, parsed_rules, dataframe, parsed_csv_issues = await _read_validation_input(file, rules, csv_issues)
    try:
        selected_group_ids = json.loads(selected_groups)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=400, detail="Selected issue groups must be valid JSON.") from error
    if not isinstance(selected_group_ids, list) or not all(
        isinstance(group_id, str) for group_id in selected_group_ids
    ):
        raise HTTPException(status_code=400, detail="Selected issue groups must be a list of IDs.")

    groups, issues = _evaluate_issues(dataframe, parsed_rules, parsed_csv_issues)
    removed_rows = _selected_row_indexes(groups, issues, selected_group_ids)
    cleaned = dataframe.drop(dataframe.index[list(removed_rows)])
    output = StringIO(newline="")
    cleaned.to_csv(output, index=False, quoting=csv.QUOTE_MINIMAL)
    filename = _safe_csv_download_stem(file.filename)
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}-cleaned.csv"'},
    )


@app.post("/api/validation-report")
async def download_validation_report(
    file: UploadFile = File(...),
    rules: str = Form(...),
    selected_groups: str = Form("[]"),
    csv_issues: str = Form("[]"),
) -> Response:
    _, parsed_rules, dataframe, parsed_csv_issues = await _read_validation_input(file, rules, csv_issues)
    try:
        selected_group_ids = json.loads(selected_groups)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=400, detail="Selected issue groups must be valid JSON.") from error
    if not isinstance(selected_group_ids, list) or not all(
        isinstance(group_id, str) for group_id in selected_group_ids
    ):
        raise HTTPException(status_code=400, detail="Selected issue groups must be a list of IDs.")

    groups, issues = _evaluate_issues(dataframe, parsed_rules, parsed_csv_issues)
    removed_rows = _selected_row_indexes(groups, issues, selected_group_ids)
    output = StringIO(newline="")
    writer = csv.writer(output)
    def write_safe_row(values: list[object]) -> None:
        safe_values = [
            f"'{value}" if isinstance(value, str) and re.match(r"^[\t\r\n ]*[=+\-@]", value) else value
            for value in values
        ]
        writer.writerow(safe_values)

    write_safe_row(["Section", "Status", "Issue group", "Severity", "Row number", "Column", "Issue", "Expected value", "Actual value", "Action"])
    write_safe_row([
        "Summary",
        "FAIL"
        if any(issue["severity"] == "error" for issue in issues)
        else "PASS WITH WARNINGS" if any(issue["severity"] == "warning" for issue in issues)
        else "PASS WITH INFO" if any(issue["severity"] == "info" for issue in issues)
        else "PASS",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        f"{len(removed_rows)} of {len(dataframe)} rows removed",
    ])
    group_labels = {group["id"]: group["label"] for group in groups}
    group_removability = {group["id"]: group["canRemove"] for group in groups}
    for group in groups:
        write_safe_row([
            "Issue group",
            "ISSUE",
            group["label"],
            group["severity"],
            "",
            "",
            f'{group["issueCount"]} issue(s), {group["affectedRows"]} affected row(s)',
            "",
            "",
            "Show matching data"
            if group["severity"] == "info"
            else "Fix CSV format and re-upload"
            if not group["canRemove"]
            else "Remove selected rows" if group["id"] in selected_group_ids else "Kept",
        ])
    for issue in issues:
        write_safe_row([
            "Issue",
            "ISSUE",
            group_labels[issue["groupId"]],
            issue["severity"],
            issue["rowNumber"],
            issue["column"],
            issue["description"],
            issue["expected"],
            issue["actual"],
            "Informational match"
            if issue["severity"] == "info"
            else "Fix CSV format and re-upload"
            if not group_removability[issue["groupId"]]
            else "Row removed" if issue["rowNumber"] - 2 in removed_rows else "Kept",
        ])
    filename = _safe_csv_download_stem(file.filename)
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}-validation-report.csv"'},
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
