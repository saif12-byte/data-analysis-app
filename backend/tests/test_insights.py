import asyncio
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from starlette.datastructures import UploadFile

from app.main import (
    DatasetQuestionRequest,
    ask_dataset_question,
    get_dataset_insights,
    upload_csv,
)
from app.services.ai import gemini
from app.services.insights import answer_dataset_question, build_insight_pack


def small_dataset():
    return pd.DataFrame(
        {
            "amount": [10, 20, 30, 1000],
            "units": [1, 2, 3, 10],
            "segment": ["A", "A", "B", "B"],
            "created_date": ["2024-01-03", "2024-01-20", "2024-02-01", "2024-02-02"],
            "email": ["secret@example.test"] * 4,
            "customer_name": ["Private Person"] * 4,
        }
    )


def test_insight_pack_matches_direct_pandas_calculations():
    dataframe = small_dataset()
    pack = build_insight_pack(dataframe)
    values = dataframe["amount"]

    assert pack["overview"] == {
        "rows": len(dataframe),
        "columns": len(dataframe.columns),
        "missing_values": int(dataframe.isna().sum().sum()),
        "duplicate_rows": int(dataframe.duplicated().sum()),
    }
    assert pack["numeric_statistics"]["amount"] == {
        "count": int(values.count()),
        "mean": float(values.mean()),
        "median": float(values.median()),
        "std": round(float(values.std()), 4),
        "min": int(values.min()),
        "max": int(values.max()),
    }
    assert pack["top_categories"]["segment"][0] == {
        "category": "A",
        "count": 2,
        "share_percent": 50,
    }
    assert pack["monthly_trend"] == [
        {"month": "2024-01", "value": 30, "change_percent": None},
        {"month": "2024-02", "value": 1030, "change_percent": 3333.3333},
    ]
    assert "email" not in json.dumps(pack)
    assert "secret@example.test" not in json.dumps(pack)
    assert "Private Person" not in json.dumps(pack)


def test_category_shares_use_the_whole_column_as_denominator():
    dataframe = pd.DataFrame({"category": [f"Group {index}" for index in range(10)]})
    pack = build_insight_pack(dataframe)

    assert len(pack["top_categories"]["category"]) == 5
    assert all(item["share_percent"] == 10 for item in pack["top_categories"]["category"])


def test_personal_columns_are_excluded_even_when_camel_cased():
    dataframe = pd.DataFrame(
        {
            "CustomerID": ["private-id"],
            "EmailAddress": ["private@example.test"],
            "Phone1": ["555-0100"],
            "FirstName": ["Private Person"],
        }
    )
    pack = build_insight_pack(dataframe)

    assert pack["numeric_statistics"] == {}
    assert pack["top_categories"] == {}
    assert "private-id" not in json.dumps(pack)
    assert "private@example.test" not in json.dumps(pack)
    assert "555-0100" not in json.dumps(pack)
    assert "Private Person" not in json.dumps(pack)


def test_question_groups_duplicate_first_names_by_country():
    dataframe = pd.DataFrame(
        {
            "Index": [1, 2, 3, 4, 5],
            "First Name": ["Alex", "Alex", "Alex", "Sam", "Sam"],
            "Country": ["US", "US", "CA", "CA", "CA"],
        }
    )
    answer = answer_dataset_question(
        dataframe,
        "How many indexes are there with same first name group by country?",
    )

    assert answer["group_by"] == ["First Name", "Country"]
    assert answer["metric"] == {"aggregation": "count", "column": "Index"}
    assert answer["results"] == [
        {"First Name": "Alex", "Country": "US", "count_Index": 2},
        {"First Name": "Sam", "Country": "CA", "count_Index": 2},
        {"First Name": "Alex", "Country": "CA", "count_Index": 1},
    ]
    assert answer["total_groups"] == 3
    assert answer["truncated"] is False


def test_question_endpoint_reads_uploaded_data_without_gemini(monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "false")
    dataframe = pd.DataFrame(
        {
            "Index": [1, 2, 3],
            "First Name": ["Alex", "Alex", "Sam"],
            "Country": ["US", "US", "CA"],
        }
    )
    content = dataframe.to_csv(index=False).encode("utf-8")
    upload = UploadFile(filename="question.csv", file=BytesIO(content))
    result = asyncio.run(
        upload_csv(
            upload,
            measure_column="auto",
            date_column="auto",
            group_column="auto",
            aggregation="sum",
            trend_chart="area",
            category_chart="bar",
        )
    )
    answer = ask_dataset_question(
        result["dataset_id"],
        DatasetQuestionRequest(
            question="How many indexes are there with same first name group by country?"
        ),
    )

    assert answer["results"][0] == {
        "First Name": "Alex",
        "Country": "US",
        "count_Index": 2,
    }


def test_100k_customer_file_pack_matches_direct_pandas():
    dataset_path = Path(__file__).resolve().parents[2] / "data" / "customers-100000.csv"
    if not dataset_path.exists():
        pytest.skip("The 100,000-row customer CSV is not present.")

    dataframe = pd.read_csv(dataset_path)
    pack = build_insight_pack(dataframe)
    assert pack["overview"] == {
        "rows": len(dataframe),
        "columns": len(dataframe.columns),
        "missing_values": int(dataframe.isna().sum().sum()),
        "duplicate_rows": int(dataframe.duplicated().sum()),
    }
    assert dataframe["Country"].nunique(dropna=True) > 20
    assert pack["numeric_statistics"] == {}
    assert pack["top_categories"] == {}
    assert pack["monthly_trend"] == []
    assert pack["correlations"] == []
    assert pack["outlier_counts"] == {}

    serialized_pack = json.dumps(pack)
    for private_column in ("Customer Id", "First Name", "Last Name", "Phone 1", "Phone 2", "Email"):
        assert private_column not in serialized_pack
    assert len(serialized_pack.encode("utf-8")) < 5000


def test_finding_number_filter_discards_invented_numbers():
    pack = build_insight_pack(small_dataset())
    valid = gemini.Finding(
        finding="The dataset contains 4 rows.",
        supporting_numbers=["4"],
    )
    invented = gemini.Finding(
        finding="The average is 999.",
        supporting_numbers=["999"],
    )
    unsupported = gemini.Finding(
        finding="The data has a notable pattern.",
        supporting_numbers=["numeric summary"],
    )

    assert gemini.filter_unverified_findings([valid, invented, unsupported], pack) == [
        valid.model_dump()
    ]


def test_ai_disabled_and_missing_key_return_rule_based_findings(monkeypatch):
    pack = build_insight_pack(small_dataset())
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    missing_key = asyncio.run(gemini.generate_ai_insights("missing-key", "hash", pack))
    assert missing_key["status"] == "missing_key"
    assert missing_key["source"] == "rules"
    assert missing_key["findings"] == pack["rule_findings"]

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("AI_ENABLED", "false")
    disabled = asyncio.run(gemini.generate_ai_insights("disabled", "hash", pack))
    assert disabled["status"] == "disabled"
    assert disabled["findings"] == pack["rule_findings"]


def test_model_receives_only_pack_and_results_are_cached(monkeypatch):
    pack = build_insight_pack(small_dataset())
    prompts = []
    calls = []

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            prompts.append(contents)
            calls.append(model)
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "findings": [
                            {
                                "finding": "The dataset contains 4 rows.",
                                "supporting_numbers": ["4"],
                            },
                            {
                                "finding": "A made-up result is 777.",
                                "supporting_numbers": ["777"],
                            },
                        ]
                    }
                ),
                usage_metadata=SimpleNamespace(
                    prompt_token_count=12,
                    candidates_token_count=8,
                ),
            )

    class FakeClient:
        def __init__(self, *, api_key, http_options):
            self.models = FakeModels()

        def close(self):
            pass

    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(gemini.genai, "Client", FakeClient)
    first = asyncio.run(gemini.generate_ai_insights("dataset-safe-prompt", "hash-a", pack))
    second = asyncio.run(gemini.generate_ai_insights("dataset-safe-prompt", "hash-a", pack))

    assert len(calls) == 1
    assert "Private Person" not in prompts[0]
    assert "secret@example.test" not in prompts[0]
    assert "10,1,A,2024-01-03" not in prompts[0]
    assert first["findings"] == [
        {
            "finding": "The dataset contains 4 rows.",
            "supporting_numbers": ["4"],
        }
    ]
    assert second["source"] == "cache"


def test_gemini_rate_limit_returns_rule_findings(monkeypatch):
    pack = build_insight_pack(small_dataset())

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            raise gemini.genai.errors.APIError(
                429,
                {"error": {"message": "rate limit"}},
            )

    class FakeClient:
        def __init__(self, *, api_key, http_options):
            self.models = FakeModels()

        def close(self):
            pass

    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(gemini.genai, "Client", FakeClient)
    result = asyncio.run(gemini.generate_ai_insights("rate-limited", "hash", pack))

    assert result["status"] == "rate_limited"
    assert result["source"] == "rules"
    assert result["findings"] == pack["rule_findings"]


def test_upload_stores_pack_for_ai_independent_get_endpoint(monkeypatch):
    monkeypatch.setenv("AI_ENABLED", "false")
    dataframe = small_dataset()
    content = dataframe.to_csv(index=False).encode("utf-8")
    upload = UploadFile(filename="test.csv", file=BytesIO(content))
    upload_result = asyncio.run(
        upload_csv(
            upload,
            measure_column="auto",
            date_column="auto",
            group_column="auto",
            aggregation="sum",
            trend_chart="area",
            category_chart="bar",
        )
    )

    response = get_dataset_insights(upload_result["dataset_id"])
    assert response["pack"]["overview"]["rows"] == len(dataframe)
    assert response["ai_enabled"] is False
