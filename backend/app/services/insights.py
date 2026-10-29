from __future__ import annotations

from collections import OrderedDict
import gzip
import re
from threading import RLock
from typing import Any
import uuid

import numpy as np
import pandas as pd
from pandas.api.types import is_bool_dtype, is_datetime64_any_dtype, is_numeric_dtype


_PRIVATE_COLUMN = re.compile(
    r"(^|[^a-z0-9])(email|e-mail|phone[0-9]*|mobile[0-9]*|telephone|name[0-9]*|address|street|"
    r"postal|postcode|zip|ssn|passport|identifier|surname|index|id)([^a-z0-9]|$)",
    re.IGNORECASE,
)
_DATE_COLUMN = re.compile(r"date|time|month|day", re.IGNORECASE)
# Uploads are temporary, so keep only recent compact summaries in memory.
_MAX_STORED_PACKS = 20
_MAX_STORED_DATASETS = 3
_PACKS: OrderedDict[str, tuple[str, dict[str, Any]]] = OrderedDict()
_DATASET_CSV: OrderedDict[str, bytes] = OrderedDict()
_PACKS_LOCK = RLock()


def _is_private_column(column: Any) -> bool:
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(column)).lower()
    return bool(_PRIVATE_COLUMN.search(normalized))


def _safe_number(value: Any) -> int | float | None:
    if pd.isna(value):
        return None
    number = float(value)
    if not np.isfinite(number):
        return None
    if number.is_integer():
        return int(number)
    return round(number, 4)


def _numeric_columns(dataframe: pd.DataFrame, columns: list[str]) -> dict[str, pd.Series]:
    numeric = {}
    for column in columns:
        values = dataframe[column]
        if is_bool_dtype(values.dtype):
            continue
        if is_numeric_dtype(values.dtype):
            converted = pd.to_numeric(values, errors="coerce")
        else:
            converted = pd.to_numeric(values, errors="coerce")
            non_missing = values.notna().sum()
            if not non_missing or converted.notna().sum() / non_missing < 0.9:
                continue
        converted = converted.replace([np.inf, -np.inf], np.nan)
        if converted.notna().any():
            numeric[column] = converted
    return numeric


def _date_columns(dataframe: pd.DataFrame, columns: list[str]) -> dict[str, pd.Series]:
    dates = {}
    for column in columns:
        values = dataframe[column]
        if is_datetime64_any_dtype(values.dtype):
            converted = pd.to_datetime(values, errors="coerce")
        elif _DATE_COLUMN.search(column):
            converted = pd.to_datetime(values, errors="coerce")
        else:
            continue
        if converted.notna().any():
            dates[column] = converted
    return dates


def _rule_findings(pack: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    overview = pack["overview"]
    findings.append(
        {
            "finding": (
                f"The dataset contains {overview['rows']} rows across "
                f"{overview['columns']} columns, with {overview['missing_values']} missing values."
            ),
            "supporting_numbers": [
                str(overview["rows"]),
                str(overview["columns"]),
                str(overview["missing_values"]),
            ],
        }
    )
    if overview["duplicate_rows"]:
        findings.append(
            {
                "finding": f"{overview['duplicate_rows']} duplicate rows were found.",
                "supporting_numbers": [str(overview["duplicate_rows"])],
            }
        )
    for column, stats in pack["numeric_statistics"].items():
        if stats["min"] is None or stats["max"] is None:
            continue
        findings.append(
            {
                "finding": (
                    f"{column} ranges from {stats['min']} to {stats['max']} "
                    f"across {stats['count']} numeric values."
                ),
                "supporting_numbers": [
                    str(stats["min"]),
                    str(stats["max"]),
                    str(stats["count"]),
                ],
            }
        )
        if len(findings) >= 4:
            break
    if pack["top_categories"]:
        column, values = next(iter(pack["top_categories"].items()))
        if values:
            top = values[0]
            findings.append(
                {
                    "finding": (
                        f"The most common value in {column} is {top['category']}, "
                        f"representing {top['share_percent']}% of rows."
                    ),
                    "supporting_numbers": [str(top["share_percent"]), str(top["count"])],
                }
            )
    if pack["monthly_trend"]:
        latest = pack["monthly_trend"][-1]
        if latest["change_percent"] is not None:
            findings.append(
                {
                    "finding": (
                        f"The latest monthly total is {latest['value']}, "
                        f"changing {latest['change_percent']}% from the previous month."
                    ),
                    "supporting_numbers": [
                        str(latest["value"]),
                        str(latest["change_percent"]),
                    ],
                }
            )
    return findings[:6]


def build_insight_pack(dataframe: pd.DataFrame) -> dict[str, Any]:
    """Create a compact, aggregate-only summary without exposing personal columns."""
    columns = [
        str(column)
        for column in dataframe.columns
        if not _is_private_column(column)
    ]
    visible = dataframe.loc[:, columns]
    numeric = _numeric_columns(visible, columns)
    date_values = _date_columns(visible, columns)

    statistics: dict[str, dict[str, Any]] = {}
    outliers: dict[str, int] = {}
    for column, values in numeric.items():
        clean = values.dropna()
        statistics[column] = {
            "count": int(clean.count()),
            "mean": _safe_number(clean.mean()),
            "median": _safe_number(clean.median()),
            "std": _safe_number(clean.std()),
            "min": _safe_number(clean.min()),
            "max": _safe_number(clean.max()),
        }
        if clean.empty:
            outliers[column] = 0
        else:
            lower_quartile = clean.quantile(0.25)
            upper_quartile = clean.quantile(0.75)
            spread = upper_quartile - lower_quartile
            outliers[column] = int(
                ((clean < lower_quartile - 1.5 * spread) |
                 (clean > upper_quartile + 1.5 * spread)).sum()
            )

    categories: dict[str, list[dict[str, Any]]] = {}
    for column in columns:
        values = visible[column]
        if column in date_values:
            continue
        unique_count = int(values.nunique(dropna=True))
        if unique_count > 20:
            continue
        counts = values.astype("string").fillna("(missing)").value_counts().head(5)
        denominator = len(values)
        categories[column] = [
            {
                "category": str(category)[:60],
                "count": int(count),
                "share_percent": _safe_number(count / denominator * 100) if denominator else 0,
            }
            for category, count in counts.items()
        ]

    monthly_trend: list[dict[str, Any]] = []
    if date_values and numeric:
        date_column = next(iter(date_values))
        value_column = next(iter(numeric))
        trend_frame = pd.DataFrame(
            {"date": date_values[date_column], "value": numeric[value_column]}
        ).dropna()
        if not trend_frame.empty:
            monthly = trend_frame.groupby(
                trend_frame["date"].dt.to_period("M"), sort=True
            )["value"].sum().tail(12)
            previous = None
            for period, value in monthly.items():
                current = _safe_number(value)
                change = (
                    _safe_number((value - previous) / abs(previous) * 100)
                    if previous is not None and previous != 0
                    else None
                )
                monthly_trend.append(
                    {"month": str(period), "value": current, "change_percent": change}
                )
                previous = value

    correlations: list[dict[str, Any]] = []
    if len(numeric) > 1:
        correlation_matrix = pd.DataFrame(numeric).corr()
        upper_triangle = np.triu(np.ones(correlation_matrix.shape), k=1).astype(bool)
        pairs = (
            correlation_matrix.where(upper_triangle)
            .stack()
            .dropna()
            .sort_values(key=lambda values: values.abs(), ascending=False)
            .head(5)
        )
        correlations = [
            {
                "column_a": str(pair[0]),
                "column_b": str(pair[1]),
                "correlation": _safe_number(value),
            }
            for pair, value in pairs.items()
        ]

    pack: dict[str, Any] = {
        "overview": {
            "rows": int(len(dataframe)),
            "columns": int(len(dataframe.columns)),
            "missing_values": int(dataframe.isna().sum().sum()),
            "duplicate_rows": int(dataframe.duplicated().sum()),
        },
        "numeric_statistics": statistics,
        "top_categories": categories,
        "monthly_trend": monthly_trend,
        "correlations": correlations,
        "outlier_counts": outliers,
    }
    pack["rule_findings"] = _rule_findings(pack)
    return pack


def store_insight_pack(
    data_hash: str, pack: dict[str, Any], csv_contents: bytes
) -> str:
    dataset_id = str(uuid.uuid4())
    with _PACKS_LOCK:
        _PACKS[dataset_id] = (data_hash, pack)
        _DATASET_CSV[dataset_id] = gzip.compress(csv_contents)
        while len(_DATASET_CSV) > _MAX_STORED_DATASETS:
            _DATASET_CSV.popitem(last=False)
        while len(_PACKS) > _MAX_STORED_PACKS:
            _PACKS.popitem(last=False)
    return dataset_id


def get_insight_pack(dataset_id: str) -> tuple[str, dict[str, Any]] | None:
    with _PACKS_LOCK:
        record = _PACKS.get(dataset_id)
        if record is not None:
            _PACKS.move_to_end(dataset_id)
        return record


def get_dataset_csv(dataset_id: str) -> bytes | None:
    with _PACKS_LOCK:
        csv_contents = _DATASET_CSV.get(dataset_id)
        if csv_contents is not None:
            _DATASET_CSV.move_to_end(dataset_id)
        return csv_contents


def answer_dataset_question(
    dataframe: pd.DataFrame, question: str, result_limit: int = 100
) -> dict[str, Any]:
    """Resolve mentioned columns and calculate a bounded grouped answer with Pandas."""
    normalized_question = re.sub(r"[^a-z0-9]+", " ", question.casefold()).strip()
    padded_question = f" {normalized_question} "
    matched_columns: list[str] = []
    aliases: dict[str, str] = {}

    for column in dataframe.columns:
        name = str(column)
        normalized_name = re.sub(r"[^a-z0-9]+", " ", name.casefold()).strip()
        variants = {normalized_name}
        words = normalized_name.split()
        if words and words[-1].endswith("s") and len(words[-1]) > 3:
            variants.add(" ".join([*words[:-1], words[-1][:-1]]))
        elif normalized_name.endswith("index"):
            variants.add(f"{normalized_name}es")
        elif normalized_name.endswith("y"):
            variants.add(f"{normalized_name[:-1]}ies")
        else:
            variants.add(f"{normalized_name}s")
        if any(f" {variant} " in padded_question for variant in variants):
            matched_columns.append(name)
            for variant in variants:
                aliases[variant] = name

    if not matched_columns:
        raise ValueError(
            "I couldn't match a column name in your question. Try using the exact column names from the CSV header."
        )

    mentioned_columns = list(dict.fromkeys(matched_columns))
    count_match = re.search(
        r"(?:how many|number of|count(?: of)?)\s+(.+?)(?:\s+(?:are|is|there|with|where|group|by|per)\b|$)",
        normalized_question,
    )
    target_column = None
    aggregation = "count"
    metric_match = re.search(
        r"\b(sum|total|average|mean|median|minimum|maximum|min|max)\b",
        normalized_question,
    )
    if metric_match:
        aggregation = {
            "total": "sum",
            "average": "mean",
            "minimum": "min",
            "maximum": "max",
        }.get(metric_match.group(1), metric_match.group(1))

    if count_match:
        target_text = f" {count_match.group(1)} "
        target_column = next(
            (
                column
                for column in mentioned_columns
                if f" {re.sub(r'[^a-z0-9]+', ' ', column.casefold()).strip()} "
                in target_text
                or any(
                    f" {alias} " in target_text and resolved == column
                    for alias, resolved in aliases.items()
                )
            ),
            None,
        )
    elif metric_match:
        target_column = next(
            (
                column
                for column in mentioned_columns
                if pd.api.types.is_numeric_dtype(dataframe[column])
            ),
            None,
        )

    group_columns = [
        column for column in mentioned_columns if column != target_column
    ]
    if not group_columns and target_column is None and len(mentioned_columns) > 1:
        group_columns = mentioned_columns
    if len(group_columns) > 2:
        raise ValueError("Please group by no more than two CSV columns at a time.")

    if aggregation != "count":
        if target_column is None:
            raise ValueError(
                "For sum, average, median, minimum, or maximum, mention a numeric column from your CSV."
            )
        values = pd.to_numeric(dataframe[target_column], errors="coerce")
        if not values.notna().any():
            raise ValueError(f"The column '{target_column}' has no numeric values.")
        if target_column in group_columns:
            group_columns.remove(target_column)
    elif target_column is not None and pd.api.types.is_numeric_dtype(dataframe[target_column]):
        aggregation = "count"
    elif target_column is not None:
        aggregation = "nunique"

    value_key = (
        f"{aggregation}_{target_column}"
        if target_column is not None
        else "row_count"
    )
    if target_column is None and aggregation != "count":
        raise ValueError("Mention a CSV column to calculate this metric.")

    working = pd.DataFrame(index=dataframe.index)
    for column in group_columns:
        working[column] = (
            dataframe[column].astype("string").fillna("(missing)")
        )
    if target_column is not None:
        if aggregation in {"sum", "mean", "median", "min", "max"}:
            working[value_key] = pd.to_numeric(dataframe[target_column], errors="coerce")
        else:
            working[value_key] = dataframe[target_column]

    if group_columns:
        grouped = working.groupby(group_columns, dropna=False, sort=False)
        if aggregation == "count" and target_column is None:
            values = grouped.size().rename(value_key)
        elif aggregation == "count":
            values = grouped[value_key].count().rename(value_key)
        elif aggregation == "nunique":
            values = grouped[value_key].nunique(dropna=True).rename(value_key)
        else:
            values = grouped[value_key].agg(aggregation).rename(value_key)
        grouped_results = values.sort_values(ascending=False, kind="stable")
        total_groups = len(grouped_results)
        selected = grouped_results.head(result_limit)
        records = []
        for group_key, value in selected.items():
            key_values = group_key if isinstance(group_key, tuple) else (group_key,)
            record = dict(zip(group_columns, key_values))
            record[value_key] = _safe_number(value)
            records.append(record)
    else:
        source = (
            pd.to_numeric(dataframe[target_column], errors="coerce")
            if target_column is not None and aggregation in {"sum", "mean", "median", "min", "max"}
            else dataframe[target_column]
            if target_column is not None
            else None
        )
        if aggregation == "count":
            value = int(len(dataframe) if source is None else source.count())
        elif aggregation == "nunique":
            value = int(source.nunique(dropna=True))
        else:
            clean = source.dropna()
            value = _safe_number(clean.agg(aggregation)) if not clean.empty else None
        records = [{value_key: value}]
        total_groups = 1

    return {
        "question": question,
        "group_by": group_columns,
        "metric": {"aggregation": aggregation, "column": target_column},
        "total_groups": total_groups,
        "results": records,
        "truncated": total_groups > result_limit,
    }
