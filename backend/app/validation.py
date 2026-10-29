from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
from fastapi import HTTPException, UploadFile

MAX_FILE_SIZE = 25 * 1024 * 1024
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
ISSUE_COLUMNS = [
    "groupId",
    "rowNumber",
    "column",
    "description",
    "actual",
    "expected",
    "severity",
    "ruleId",
    "_phase",
    "_order",
    "_sort_row",
]
logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    issue_groups: list[dict]
    issues: pd.DataFrame
    rule_results: list[dict]
    summary: dict[str, int]
    automatic: dict


def _log_validation_timing(stage: str, started_at: float, **details: object) -> None:
    if os.getenv("VALIDATION_TIMING") != "1":
        return
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    detail_text = " ".join(f"{key}={value}" for key, value in details.items())
    logger.info(
        "validation_timing stage=%s duration_ms=%.3f %s",
        stage,
        elapsed_ms,
        detail_text,
    )


def validate_rule_payload(rules: list) -> None:
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


def _text_values(series: pd.Series) -> pd.Series:
    return series.astype("string").fillna("").str.strip()


def _equals_mask(values: pd.Series, expected: str) -> pd.Series:
    return values.str.casefold().eq(expected.strip().casefold())


def _not_equals_mask(values: pd.Series, expected: str) -> pd.Series:
    return ~_equals_mask(values, expected)


def _contains_mask(values: pd.Series, expected: str) -> pd.Series:
    return values.str.casefold().str.contains(re.escape(expected.casefold()), regex=True)


def _not_contains_mask(values: pd.Series, expected: str) -> pd.Series:
    return ~_contains_mask(values, expected)


def _numeric_mask(values: pd.Series, expected: str, condition: str) -> pd.Series:
    numeric_values = pd.to_numeric(
        values.str.replace(",", "", regex=False), errors="coerce"
    )
    finite_values = numeric_values.notna() & np.isfinite(numeric_values)
    threshold = float(expected)
    comparisons = (
        numeric_values.gt(threshold)
        if condition == "greater_than"
        else numeric_values.lt(threshold)
    )
    return finite_values & comparisons


def _greater_than_mask(values: pd.Series, expected: str) -> pd.Series:
    return _numeric_mask(values, expected, "greater_than")


def _less_than_mask(values: pd.Series, expected: str) -> pd.Series:
    return _numeric_mask(values, expected, "less_than")


def _is_empty_mask(values: pd.Series, _: str) -> pd.Series:
    return values.eq("")


def _is_not_empty_mask(values: pd.Series, _: str) -> pd.Series:
    return values.ne("")


RULE_EVALUATORS: dict[str, Callable[[pd.Series, str], pd.Series]] = {
    "equals": _equals_mask,
    "not_equals": _not_equals_mask,
    "contains": _contains_mask,
    "not_contains": _not_contains_mask,
    "greater_than": _greater_than_mask,
    "less_than": _less_than_mask,
    "is_empty": _is_empty_mask,
    "is_not_empty": _is_not_empty_mask,
}


def _infer_column_types(series: pd.Series) -> pd.Series:
    values = _text_values(series)
    populated = values.ne("")
    inferred = pd.Series(pd.NA, index=values.index, dtype="string")
    inferred.loc[populated] = "Text"

    boolean_mask = values.str.fullmatch(r"(?i:true|false)", na=False)
    inferred.loc[boolean_mask] = "Boolean"

    date_mask = values.str.fullmatch(
        r"(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[T ].*)?|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})",
        na=False,
    )
    date_values = pd.to_datetime(
        values.where(date_mask), errors="coerce", format="mixed"
    )
    inferred.loc[date_values.notna()] = "Date"

    numeric_text = values.str.replace(r"[,\s₹$€£%]", "", regex=True)
    numeric_values = pd.to_numeric(numeric_text, errors="coerce")
    numeric_mask = populated & numeric_values.notna() & np.isfinite(numeric_values)
    inferred.loc[numeric_mask] = "Number"
    return inferred


def _issue_frame(
    positions: np.ndarray,
    group_id: str,
    column: str,
    description: str,
    actual: pd.Series | None,
    expected: str,
    severity: str,
    rule_id: str,
    phase: int,
    order: int,
) -> pd.DataFrame:
    if not len(positions):
        return pd.DataFrame(columns=ISSUE_COLUMNS)
    return pd.DataFrame(
        {
            "groupId": group_id,
            "rowNumber": positions + 2,
            "column": column,
            "description": description,
            "actual": (
                actual.iloc[positions].astype(str).to_numpy()
                if actual is not None
                else np.full(len(positions), "", dtype=object)
            ),
            "expected": expected,
            "severity": severity,
            "ruleId": rule_id,
            "_phase": phase,
            "_order": order,
            "_sort_row": positions + 2,
        }
    )


def _positions(mask: pd.Series) -> np.ndarray:
    return np.flatnonzero(mask.fillna(False).to_numpy(dtype=bool))


def _group_label(group_id: str, rules_by_id: dict[str, dict]) -> str:
    if group_id.startswith("custom:"):
        rule = rules_by_id[group_id.removeprefix("custom:")]
        expected = rule.get("value", "")
        return f'{rule["column"]} {rule["condition"]} {expected}'.strip()
    return {
        "automatic:duplicates": "Duplicate rows",
        "automatic:missing": "Missing values",
        "automatic:mixed_types": "Mixed column types",
        "automatic:csv_rows": "CSV row issues",
    }[group_id]


def evaluate_dataset(
    dataframe: pd.DataFrame, rules: list[dict], csv_issues: list[dict]
) -> ValidationResult:
    evaluation_started_at = time.perf_counter()
    columns = [str(column) for column in dataframe.columns]
    normalized = pd.DataFrame(
        {
            column: _text_values(dataframe[original_column])
            for column, original_column in zip(columns, dataframe.columns)
        },
        index=dataframe.index,
    )
    issue_frames: list[pd.DataFrame] = []
    rule_results = []
    rules_by_id = {}

    duplicate_positions = _positions(normalized.duplicated(keep="first"))
    issue_frames.append(
        _issue_frame(
            duplicate_positions,
            "automatic:duplicates",
            "",
            "Duplicate of an earlier row",
            None,
            "",
            "warning",
            "",
            0,
            0,
        )
    )

    for rule_index, rule in enumerate(rules):
        rule_started_at = time.perf_counter()
        rule_id = str(rule.get("id", rule_index + 1))
        rules_by_id[rule_id] = rule
        expected = rule.get("value", "")
        matches = RULE_EVALUATORS[rule["condition"]](
            normalized[rule["column"]], expected
        )
        issue_mask = matches if rule["severity"] == "info" else ~matches
        issue_positions = _positions(issue_mask)
        issue_frames.append(
            _issue_frame(
                issue_positions,
                f"custom:{rule_id}",
                rule["column"],
                (
                    f'{rule["column"]} matches {rule["condition"]}'
                    if rule["severity"] == "info"
                    else f'{rule["column"]} does not satisfy {rule["condition"]}'
                ) + (f" {expected}" if expected else ""),
                dataframe[rule["column"]],
                expected,
                rule["severity"],
                rule_id,
                0,
                rule_index + 1,
            )
        )
        rule_results.append(
            {
                "id": rule_id,
                "column": rule["column"],
                "condition": rule["condition"],
                "value": expected,
                "severity": rule["severity"],
                "failedRows": 0 if rule["severity"] == "info" else len(issue_positions),
                "matchingRows": len(issue_positions) if rule["severity"] == "info" else 0,
            }
        )
        _log_validation_timing(
            "rule",
            rule_started_at,
            rule_id=rule_id,
            condition=rule["condition"],
            rows=len(dataframe),
        )

    type_expectations = []
    missing_masks = []
    mixed_masks = []
    for column_index, column in enumerate(columns):
        values = normalized[column]
        missing_mask = values.eq("")
        missing_masks.append(missing_mask)
        missing_positions = _positions(missing_mask)
        issue_frames.append(
            _issue_frame(
                missing_positions,
                "automatic:missing",
                column,
                "Missing value",
                dataframe[column],
                "",
                "error",
                "",
                0,
                len(rules) + 1 + column_index,
            )
        )

        types = _infer_column_types(dataframe[column])
        counts = types.value_counts(sort=False, dropna=True)
        expected_type = counts.idxmax() if not counts.empty else None
        type_expectations.append(expected_type)
        mixed_mask = types.notna() & types.ne(expected_type) if expected_type else types.notna() & False
        mixed_masks.append(mixed_mask)
        mixed_positions = _positions(mixed_mask)
        mixed_frame = _issue_frame(
            mixed_positions,
            "automatic:mixed_types",
            column,
            "",
            dataframe[column],
            str(expected_type or ""),
            "warning",
            "",
            1,
            column_index,
        )
        if not mixed_frame.empty:
            mixed_frame["description"] = types.iloc[mixed_positions].reset_index(
                drop=True
            ).astype("string").radd(f"Expected {expected_type}, found ")
        issue_frames.append(mixed_frame)

    if csv_issues:
        issue_frames.append(
            pd.DataFrame(
                [
                    {
                        "groupId": "automatic:csv_rows",
                        "rowNumber": issue["rowNumber"],
                        "column": "",
                        "description": f'{issue["code"]}: {issue["description"]}',
                        "actual": "",
                        "expected": "",
                        "severity": "error",
                        "ruleId": "",
                        "_phase": 2,
                        "_order": index,
                        "_sort_row": 0,
                    }
                    for index, issue in enumerate(csv_issues)
                ],
                columns=ISSUE_COLUMNS,
            )
        )

    nonempty_frames = [frame for frame in issue_frames if not frame.empty]
    issues = (
        pd.concat(nonempty_frames, ignore_index=True)
        .sort_values(
            ["_phase", "_sort_row", "_order"],
            kind="stable",
            na_position="last",
        )
        .reset_index(drop=True)
        if nonempty_frames
        else pd.DataFrame(columns=ISSUE_COLUMNS)
    )

    issue_groups = []
    group_rule_lookup = {f"custom:{rule_id}": rule for rule_id, rule in rules_by_id.items()}
    for group_id, group_issues in issues.groupby("groupId", sort=False):
        first_issue = group_issues.iloc[0]
        row_numbers = np.sort(
            group_issues["rowNumber"].dropna().unique().astype(np.int64)
        )
        issue_columns = [
            column
            for column in group_issues["column"].drop_duplicates().tolist()
            if column
        ]
        sample_columns = issue_columns or columns
        samples = []
        sample_rows = row_numbers[:MAX_ISSUE_SAMPLES].tolist()
        for row_number in sample_rows:
            row_offset = row_number - 2
            values = (
                {
                    column: str(dataframe.iloc[row_offset][column])
                    for column in sample_columns
                }
                if 0 <= row_offset < len(dataframe)
                else {}
            )
            samples.append({"rowNumber": row_number, "values": values})

        rule = group_rule_lookup.get(group_id)
        label = _group_label(group_id, rules_by_id)
        issue_groups.append(
            {
                "id": group_id,
                "label": label,
                "ruleName": label,
                "column": rule["column"] if rule else (
                    issue_columns[0] if len(issue_columns) == 1 else None
                ),
                "severity": first_issue["severity"],
                "canRemove": (
                    first_issue["severity"] != "info"
                    and group_id != "automatic:csv_rows"
                ),
                "affectedRows": len(row_numbers),
                "issueCount": len(group_issues),
                "sampleRows": sample_rows,
                "samples": samples,
            }
        )

    custom_issues = issues[issues["groupId"].str.startswith("custom:")]
    error_rows = (
        custom_issues.loc[custom_issues["severity"].eq("error"), "rowNumber"]
        .dropna()
        .to_numpy(dtype=np.int64)
    )
    warning_rows = (
        custom_issues.loc[custom_issues["severity"].eq("warning"), "rowNumber"]
        .dropna()
        .to_numpy(dtype=np.int64)
    )
    info_rows = (
        custom_issues.loc[custom_issues["severity"].eq("info"), "rowNumber"]
        .dropna()
        .to_numpy(dtype=np.int64)
    )
    failed_rows = np.union1d(error_rows, warning_rows)
    summary = {
        "passedRows": len(dataframe) - len(failed_rows),
        "failedRows": len(failed_rows),
        "errorRows": len(error_rows),
        "warningRows": len(warning_rows),
        "infoRows": len(info_rows),
    }

    column_results = []
    missing_cells = 0
    for index, column in enumerate(columns):
        missing_count = int(missing_masks[index].sum())
        missing_cells += missing_count
        expected_type = type_expectations[index]
        column_results.append(
            {
                "name": column,
                "missing": missing_count,
                "inferredType": expected_type or "Empty",
                "mixedTypes": int(mixed_masks[index].sum()),
            }
        )
    automatic = {
        "rows": len(dataframe),
        "columns": len(columns),
        "missingCells": missing_cells,
        "duplicateRows": len(duplicate_positions),
        "malformedRows": len(csv_issues),
        "csvIssues": csv_issues,
        "columnResults": column_results,
    }
    _log_validation_timing(
        "issue_evaluation", evaluation_started_at, issues=len(issues)
    )
    return ValidationResult(
        issue_groups=issue_groups,
        issues=issues,
        rule_results=rule_results,
        summary=summary,
        automatic=automatic,
    )


async def read_validation_input(
    file: UploadFile, rules: str, csv_issues: str
) -> tuple[bytes, list, pd.DataFrame, list[dict]]:
    if Path(file.filename or "").suffix.lower() != ".csv":
        raise HTTPException(status_code=415, detail="Upload a .csv file.")

    upload_read_started_at = time.perf_counter()
    contents = await file.read(MAX_FILE_SIZE + 1)
    _log_validation_timing("upload_read", upload_read_started_at, bytes=len(contents))
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="CSV files must be 25 MB or smaller.")
    if not contents:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    try:
        parsed_rules = json.loads(rules)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=400, detail="Validation rules must be valid JSON.") from error
    validate_rule_payload(parsed_rules)

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

    read_started_at = time.perf_counter()
    csv_parse_started_at = time.perf_counter()
    read_options = {
        "encoding": "utf-8-sig",
        "dtype": str,
        "keep_default_na": False,
        "skip_blank_lines": False,
    }
    try:
        dataframe = pd.read_csv(BytesIO(contents), engine="pyarrow", **read_options)
    except Exception as error:
        logger.info("pyarrow CSV parsing unavailable; falling back to pandas default: %s", error)
        try:
            dataframe = pd.read_csv(BytesIO(contents), **read_options)
        except (UnicodeDecodeError, pd.errors.ParserError, ValueError) as fallback_error:
            raise HTTPException(
                status_code=400,
                detail="Could not read this CSV. Check that it is valid UTF-8 CSV data.",
            ) from fallback_error

    dataframe = dataframe.fillna("")
    _log_validation_timing(
        "csv_parse",
        csv_parse_started_at,
        rows=len(dataframe),
        columns=len(dataframe.columns),
    )
    if dataframe.empty or len(dataframe.columns) == 0:
        raise HTTPException(status_code=400, detail="This CSV has column names but no data rows.")

    category_started_at = time.perf_counter()
    category_limit = min(1000, max(1, int(len(dataframe) * 0.02)))
    sample_size = min(len(dataframe), 1024)
    sample_limit = max(8, min(category_limit, sample_size // 4))
    for column in dataframe.columns:
        sample_unique = dataframe[column].iloc[:sample_size].nunique(dropna=False)
        if (
            sample_unique <= sample_limit
            and dataframe[column].nunique(dropna=False) <= category_limit
        ):
            dataframe[column] = dataframe[column].astype("category")
    _log_validation_timing(
        "category_cast", category_started_at, columns=len(dataframe.columns)
    )

    for index, rule in enumerate(parsed_rules, start=1):
        if rule["column"] not in dataframe.columns:
            raise HTTPException(
                status_code=400,
                detail=f'Rule {index} refers to missing column "{rule["column"]}".',
            )

    _log_validation_timing(
        "dataframe_read",
        read_started_at,
        rows=len(dataframe),
        columns=len(dataframe.columns),
    )
    return contents, parsed_rules, dataframe, parsed_csv_issues


def parse_selected_groups(selected_groups: str) -> list[str]:
    try:
        group_ids = json.loads(selected_groups)
    except json.JSONDecodeError as error:
        raise HTTPException(
            status_code=400, detail="Selected issue groups must be valid JSON."
        ) from error
    if not isinstance(group_ids, list) or not all(
        isinstance(group_id, str) for group_id in group_ids
    ):
        raise HTTPException(
            status_code=400, detail="Selected issue groups must be a list of IDs."
        )
    return group_ids


def selected_row_indexes(
    result: ValidationResult, selected_group_ids: list[str]
) -> np.ndarray:
    available_groups = {group["id"] for group in result.issue_groups}
    removable_groups = {group["id"] for group in result.issue_groups if group["canRemove"]}
    if any(
        group_id not in available_groups or group_id not in removable_groups
        for group_id in selected_group_ids
    ):
        raise HTTPException(status_code=400, detail="A selected issue group is not available.")
    selected_issues = result.issues[result.issues["groupId"].isin(selected_group_ids)]
    return (
        selected_issues["rowNumber"]
        .dropna()
        .unique()
        .astype(np.int64)
        - 2
    )


def issue_group_page(
    dataframe: pd.DataFrame,
    rules: list[dict],
    csv_issues: list[dict],
    group_id: str,
    page: int,
    page_size: int,
) -> dict:
    start = (page - 1) * page_size
    columns = [str(column) for column in dataframe.columns]
    row_issues: dict[int, list[dict]] = {}

    if group_id.startswith("custom:"):
        rule_entry = next(
            (
                (index, rule)
                for index, rule in enumerate(rules, start=1)
                if f'custom:{rule.get("id", str(index))}' == group_id
            ),
            None,
        )
        if rule_entry is None:
            raise HTTPException(status_code=404, detail="Issue group not found.")
        rule_index, rule = rule_entry
        values = _text_values(dataframe[rule["column"]])
        matches = RULE_EVALUATORS[rule["condition"]](values, rule.get("value", ""))
        issue_mask = matches if rule["severity"] == "info" else ~matches
        positions = _positions(issue_mask)
        for position in positions[start : start + page_size]:
            row_number = int(position) + 2
            actual = str(dataframe[rule["column"]].iloc[position])
            expected = rule.get("value", "")
            row_issues[row_number] = [
                {
                    "column": rule["column"],
                    "description": (
                        f'{rule["column"]} matches {rule["condition"]}'
                        if rule["severity"] == "info"
                        else f'{rule["column"]} does not satisfy {rule["condition"]}'
                    ) + (f" {expected}" if expected else ""),
                    "actual": actual,
                    "expected": expected,
                    "severity": rule["severity"],
                    "ruleId": str(rule.get("id", rule_index)),
                }
            ]
        total_rows = len(positions)
        requested_rows = [int(position) + 2 for position in positions[start : start + page_size]]
    elif group_id == "automatic:duplicates":
        normalized = pd.DataFrame(
            {column: _text_values(dataframe[column]) for column in dataframe.columns},
            index=dataframe.index,
        )
        positions = _positions(normalized.duplicated(keep="first"))
        requested_positions = positions[start : start + page_size]
        requested_rows = [int(position) + 2 for position in requested_positions]
        total_rows = len(positions)
        for row_number in requested_rows:
            row_issues[row_number] = [
                {
                    "column": "",
                    "description": "Duplicate of an earlier row",
                    "actual": "",
                    "expected": "",
                    "severity": "warning",
                    "ruleId": "",
                }
            ]
    elif group_id == "automatic:missing":
        normalized = pd.DataFrame(
            {column: _text_values(dataframe[column]) for column in dataframe.columns},
            index=dataframe.index,
        )
        missing = normalized.eq("")
        positions = _positions(missing.any(axis=1))
        requested_positions = positions[start : start + page_size]
        requested_rows = [int(position) + 2 for position in requested_positions]
        total_rows = len(positions)
        for position, row_number in zip(requested_positions, requested_rows):
            row_issues[row_number] = [
                {
                    "column": column,
                    "description": "Missing value",
                    "actual": str(dataframe[column].iloc[position]),
                    "expected": "",
                    "severity": "error",
                    "ruleId": "",
                }
                for column in columns
                if missing[column].iloc[position]
            ]
    elif group_id == "automatic:mixed_types":
        type_masks = {}
        for column in columns:
            types = _infer_column_types(dataframe[column])
            counts = types.value_counts(sort=False, dropna=True)
            expected_type = counts.idxmax() if not counts.empty else None
            if expected_type:
                type_masks[column] = (
                    types,
                    expected_type,
                    types.notna() & types.ne(expected_type),
                )
        mixed = pd.DataFrame(
            {column: values[2] for column, values in type_masks.items()},
            index=dataframe.index,
        )
        positions = _positions(mixed.any(axis=1)) if not mixed.empty else np.array([], dtype=int)
        requested_positions = positions[start : start + page_size]
        requested_rows = [int(position) + 2 for position in requested_positions]
        total_rows = len(positions)
        for position, row_number in zip(requested_positions, requested_rows):
            row_issues[row_number] = [
                {
                    "column": column,
                    "description": f"Expected {expected_type}, found {types.iloc[position]}",
                    "actual": str(dataframe[column].iloc[position]),
                    "expected": str(expected_type),
                    "severity": "warning",
                    "ruleId": "",
                }
                for column, (types, expected_type, mask) in type_masks.items()
                if mask.iloc[position]
            ]
    elif group_id == "automatic:csv_rows" and csv_issues:
        row_numbers = np.unique(
            np.asarray(
                [
                    issue["rowNumber"]
                    for issue in csv_issues
                    if issue["rowNumber"] is not None
                ],
                dtype=np.int64,
            )
        )
        requested_rows = row_numbers[start : start + page_size].tolist()
        total_rows = len(row_numbers)
        for row_number in requested_rows:
            row_issues[int(row_number)] = [
                {
                    "column": "",
                    "description": f'{issue["code"]}: {issue["description"]}',
                    "actual": "",
                    "expected": "",
                    "severity": "error",
                    "ruleId": "",
                }
                for issue in csv_issues
                if issue["rowNumber"] == row_number
            ]
    else:
        raise HTTPException(status_code=404, detail="Issue group not found.")

    rows = []
    for row_number in requested_rows:
        row_offset = row_number - 2
        source_values = (
            {
                str(column): str(value)
                for column, value in dataframe.iloc[row_offset].items()
            }
            if 0 <= row_offset < len(dataframe)
            else {}
        )
        rows.append(
            {
                "rowNumber": row_number,
                "values": source_values,
                "issues": row_issues[row_number],
            }
        )
    return {
        "groupId": group_id,
        "totalRows": total_rows,
        "page": page,
        "pageSize": page_size,
        "rows": rows,
    }


def build_validation_report(
    dataframe: pd.DataFrame,
    result: ValidationResult,
    selected_group_ids: list[str],
    removed_rows: np.ndarray,
) -> pd.DataFrame:
    header = [
        "Section",
        "Status",
        "Issue group",
        "Severity",
        "Row number",
        "Column",
        "Issue",
        "Expected value",
        "Actual value",
        "Action",
    ]
    issues = result.issues
    if issues["severity"].eq("error").any():
        status = "FAIL"
    elif issues["severity"].eq("warning").any():
        status = "PASS WITH WARNINGS"
    elif issues["severity"].eq("info").any():
        status = "PASS WITH INFO"
    else:
        status = "PASS"
    rows = [
        [
            "Summary",
            status,
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            f"{len(removed_rows)} of {len(dataframe)} rows removed",
        ]
    ]
    group_by_id = {group["id"]: group for group in result.issue_groups}
    for group in result.issue_groups:
        rows.append(
            [
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
                else "Remove selected rows"
                if group["id"] in selected_group_ids
                else "Kept",
            ]
        )
    group_rows = pd.DataFrame(rows, columns=header)
    if not issues.empty:
        removed = issues["rowNumber"].sub(2).isin(removed_rows)
        removability = issues["groupId"].map(
            {group_id: group["canRemove"] for group_id, group in group_by_id.items()}
        )
        actions = np.select(
            [
                issues["severity"].eq("info"),
                ~removability,
                removed,
            ],
            [
                "Informational match",
                "Fix CSV format and re-upload",
                "Row removed",
            ],
            default="Kept",
        )
        issue_rows = pd.DataFrame(
            {
                "Section": "Issue",
                "Status": "ISSUE",
                "Issue group": issues["groupId"].map(
                    {group_id: group["label"] for group_id, group in group_by_id.items()}
                ),
                "Severity": issues["severity"],
                "Row number": issues["rowNumber"]
                .astype("Int64")
                .astype("string")
                .fillna(""),
                "Column": issues["column"],
                "Issue": issues["description"],
                "Expected value": issues["expected"],
                "Actual value": issues["actual"],
                "Action": actions,
            }
        )
        group_rows = pd.concat([group_rows, issue_rows], ignore_index=True)

    formula_prefix = re.compile(r"^[\t\r\n ]*[=+\-@]")
    for column in header:
        values = group_rows[column]
        string_values = values.astype("string")
        unsafe = string_values.str.match(formula_prefix, na=False)
        group_rows.loc[unsafe, column] = "'" + string_values.loc[unsafe]
    return group_rows
