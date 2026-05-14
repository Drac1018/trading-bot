from __future__ import annotations

from trading_mvp.providers import OpenAIProvider
from trading_mvp.schemas import (
    AIMarketSettingsRecommendation,
    IntegrationSuggestionBatch,
    TradeDecision,
)


def test_openai_trade_decision_schema_marks_all_properties_required() -> None:
    schema = OpenAIProvider._build_strict_json_schema(TradeDecision)

    properties = schema["properties"]
    assert set(schema["required"]) == set(properties.keys())
    assert schema["additionalProperties"] is False
    assert "default" not in properties["entry_zone_min"]
    assert "null" in {variant["type"] for variant in properties["entry_zone_min"]["anyOf"]}
    assert "sample_threshold_satisfied" not in properties
    assert "expected_payoff_efficiency_hint_summary" not in properties
    assert "sample_threshold_satisfied" not in schema["required"]
    assert "expected_payoff_efficiency_hint_summary" not in schema["required"]
    for field_name in (
        "strategy_id",
        "regime",
        "reason_summary",
        "entry_intent",
        "entry_zone",
        "invalidation_level",
        "risk_notes",
        "required_confirmations",
        "hard_blocks_observed",
    ):
        assert field_name in properties


def test_openai_trading_decision_system_message_starts_with_reviewer_contract() -> None:
    instructions = "You are a Senior Quant Risk Reviewer for a Binance Futures short-term trading system. Review."

    message = OpenAIProvider._build_system_message("trading_decision", instructions)

    assert message.startswith("You are a Senior Quant Risk Reviewer for a Binance Futures short-term trading system.")
    assert "Return only valid JSON that strictly matches the provided schema." in message
    assert OpenAIProvider._build_system_message("chief_review", instructions).startswith("Return only valid JSON")


def test_openai_market_settings_advisor_schema_is_profile_only() -> None:
    schema = OpenAIProvider._build_strict_json_schema(AIMarketSettingsRecommendation)

    properties = schema["properties"]
    assert set(schema["required"]) == set(properties.keys())
    assert schema["additionalProperties"] is False
    assert "recommended_profile_id" in properties
    assert "suggested_new_entry_policy" in properties
    for forbidden_raw_field in (
        "slippage_threshold_pct",
        "max_leverage",
        "position_size",
        "risk_pct",
        "rr_threshold",
        "loss_limit",
    ):
        assert forbidden_raw_field not in properties


def test_openai_market_settings_advisor_system_message_uses_reviewer_contract() -> None:
    instructions = "You are a Senior Quant Risk Reviewer for a Binance Futures short-term trading system. Advise."

    message = OpenAIProvider._build_system_message("market_settings_advisor", instructions)

    assert message.startswith("You are a Senior Quant Risk Reviewer for a Binance Futures short-term trading system.")
    assert "Return only valid JSON that strictly matches the provided schema." in message


def test_openai_batch_schema_normalizes_nested_object_requirements() -> None:
    schema = OpenAIProvider._build_strict_json_schema(IntegrationSuggestionBatch)

    item_schema = schema["$defs"]["IntegrationSuggestion"]
    assert "$defs" in schema
    assert set(item_schema["required"]) == set(item_schema["properties"].keys())
    assert item_schema["additionalProperties"] is False
