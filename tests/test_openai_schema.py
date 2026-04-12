from __future__ import annotations

from trading_mvp.providers import OpenAIProvider
from trading_mvp.schemas import ProductBacklogBatch, TradeDecision


def test_openai_trade_decision_schema_marks_all_properties_required() -> None:
    schema = OpenAIProvider._build_strict_json_schema(TradeDecision)

    properties = schema["properties"]
    assert set(schema["required"]) == set(properties.keys())
    assert schema["additionalProperties"] is False
    assert "default" not in properties["entry_zone_min"]
    assert "null" in {variant["type"] for variant in properties["entry_zone_min"]["anyOf"]}


def test_openai_batch_schema_normalizes_nested_object_requirements() -> None:
    schema = OpenAIProvider._build_strict_json_schema(ProductBacklogBatch)

    item_schema = schema["$defs"]["ProductBacklogItem"]
    assert "$defs" in schema
    assert set(item_schema["required"]) == set(item_schema["properties"].keys())
    assert item_schema["additionalProperties"] is False
