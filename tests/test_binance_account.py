from __future__ import annotations

from datetime import timedelta

from trading_mvp.models import Order, PnLSnapshot, Position, RiskCheck
from trading_mvp.schemas import BinanceAccountAsset, BinanceAccountResponse, BinanceAccountSummary
from trading_mvp.services.binance_account import (
    get_binance_account_snapshot,
    get_cached_binance_account_snapshot,
    get_local_binance_account_snapshot,
    mark_binance_account_refresh_requested,
    store_binance_account_cache_failure,
    store_binance_account_cache_result,
)
from trading_mvp.services.secret_store import encrypt_secret
from trading_mvp.services.settings import get_or_create_settings
from trading_mvp.time_utils import utcnow_naive


def test_binance_account_snapshot_returns_disconnected_without_credentials(db_session) -> None:
    payload = get_binance_account_snapshot(db_session)

    assert payload.summary.connected is False
    assert payload.assets == []
    assert payload.positions == []
    assert payload.open_orders == []


def test_local_binance_account_snapshot_uses_cached_rows_without_exchange_call(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    db_session.add(settings_row)
    db_session.add(
        PnLSnapshot(
            snapshot_date=utcnow_naive().date(),
            equity=1012.5,
            cash_balance=1000.0,
            wallet_balance=1000.0,
            available_balance=925.0,
            gross_realized_pnl=0.0,
            fee_total=0.0,
            funding_total=0.0,
            net_pnl=0.0,
            realized_pnl=0.0,
            unrealized_pnl=12.5,
            daily_pnl=0.0,
            cumulative_pnl=0.0,
            consecutive_losses=0,
        )
    )
    db_session.add(
        Position(
            symbol="BTCUSDT",
            mode="live",
            side="long",
            status="open",
            quantity=0.01,
            entry_price=68000.0,
            mark_price=68250.0,
            leverage=2.0,
            stop_loss=67000.0,
            take_profit=70000.0,
            unrealized_pnl=12.5,
            metadata_json={"margin_type": "cross"},
        )
    )
    db_session.add(
        Order(
            symbol="BTCUSDT",
            side="sell",
            order_type="stop_market",
            mode="live",
            status="pending",
            reduce_only=True,
            close_only=True,
            requested_quantity=0.01,
            requested_price=67000.0,
            filled_quantity=0.0,
            average_fill_price=0.0,
            reason_codes=["PROTECTION_STOP"],
            metadata_json={},
        )
    )
    db_session.flush()

    payload = get_local_binance_account_snapshot(db_session)

    assert payload.summary.total_wallet_balance == 1000.0
    assert payload.summary.available_balance == 925.0
    assert payload.summary.total_unrealized_profit == 12.5
    assert payload.summary.total_margin_balance == 1012.5
    assert payload.summary.asset_count == 1
    assert payload.summary.open_positions == 1
    assert payload.summary.open_orders == 1
    assert payload.assets[0].asset == "USDT"
    assert payload.positions[0].symbol == "BTCUSDT"
    assert payload.open_orders[0].reduce_only is True


def test_local_binance_account_snapshot_excludes_exchange_final_orders(db_session) -> None:
    get_or_create_settings(db_session)
    db_session.add_all(
        [
            Order(
                symbol="BTCUSDT",
                side="buy",
                order_type="limit",
                mode="live",
                status="pending",
                exchange_status="NEW",
                requested_quantity=0.01,
                requested_price=68000.0,
                filled_quantity=0.0,
                average_fill_price=0.0,
                reduce_only=False,
                close_only=False,
                reason_codes=[],
                metadata_json={},
            ),
            Order(
                symbol="SOLUSDT",
                side="sell",
                order_type="stop_market",
                mode="live",
                status="finished",
                exchange_status="FINISHED",
                requested_quantity=1.0,
                requested_price=85.0,
                filled_quantity=0.0,
                average_fill_price=0.0,
                reduce_only=True,
                close_only=True,
                reason_codes=[],
                metadata_json={},
            ),
            Order(
                symbol="XRPUSDT",
                side="sell",
                order_type="stop_market",
                mode="live",
                status="pending",
                exchange_status="FINISHED",
                requested_quantity=231.5,
                requested_price=1.45,
                filled_quantity=0.0,
                average_fill_price=0.0,
                reduce_only=True,
                close_only=True,
                reason_codes=[],
                metadata_json={},
            ),
        ]
    )
    db_session.flush()

    payload = get_local_binance_account_snapshot(db_session)

    assert payload.summary.open_orders == 1
    assert [order.symbol for order in payload.open_orders] == ["BTCUSDT"]


def test_binance_account_snapshot_builds_summary_from_exchange(monkeypatch, db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    settings_row.trading_paused = True
    settings_row.pause_reason_code = "MANUAL_USER_REQUEST"
    db_session.add(settings_row)
    db_session.flush()
    db_session.add(
        RiskCheck(
            symbol="BTCUSDT",
            decision="long",
            allowed=False,
            reason_codes=["TRADING_PAUSED"],
            approved_risk_pct=0.0,
            approved_leverage=0.0,
            payload={"reason_codes": ["TRADING_PAUSED"]},
        )
    )
    db_session.flush()

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_account_info(self):
            return {
                "canTrade": True,
                "feeTier": 1,
                "totalWalletBalance": "1250.5",
                "availableBalance": "930.25",
                "totalUnrealizedProfit": "12.75",
                "totalMarginBalance": "1263.25",
                "totalPositionInitialMargin": "110.0",
                "totalOpenOrderInitialMargin": "35.5",
                "totalMaintMargin": "20.0",
                "assets": [
                    {
                        "asset": "USDT",
                        "walletBalance": "1250.5",
                        "availableBalance": "930.25",
                        "marginBalance": "1263.25",
                        "unrealizedProfit": "12.75",
                        "maxWithdrawAmount": "900.0",
                    },
                    {
                        "asset": "BNB",
                        "walletBalance": "0",
                        "availableBalance": "0",
                        "marginBalance": "0",
                        "unrealizedProfit": "0",
                        "maxWithdrawAmount": "0",
                    },
                ],
            }

        def get_position_information(self):
            return [
                {
                    "symbol": "BTCUSDT",
                    "positionAmt": "0.01",
                    "entryPrice": "68000",
                    "markPrice": "68200",
                    "liquidationPrice": "59000",
                    "leverage": "2",
                    "unRealizedProfit": "2.0",
                    "isolatedMargin": "25",
                    "notional": "682",
                    "marginType": "cross",
                },
                {
                    "symbol": "ETHUSDT",
                    "positionAmt": "0",
                    "entryPrice": "0",
                    "markPrice": "0",
                    "liquidationPrice": "0",
                    "leverage": "2",
                    "unRealizedProfit": "0",
                    "isolatedMargin": "0",
                    "notional": "0",
                    "marginType": "cross",
                },
            ]

        def get_open_orders(self):
            return [
                {
                    "symbol": "BTCUSDT",
                    "side": "SELL",
                    "type": "STOP_MARKET",
                    "status": "NEW",
                    "price": "0",
                    "stopPrice": "66500",
                    "origQty": "0.01",
                    "executedQty": "0",
                    "reduceOnly": True,
                    "closePosition": True,
                    "timeInForce": "GTC",
                    "updateTime": 1712620000000,
                }
            ]

    monkeypatch.setattr("trading_mvp.services.binance_account.BinanceClient", FakeClient)

    payload = get_binance_account_snapshot(db_session)

    assert payload.summary.connected is True
    assert payload.summary.can_trade is True
    assert payload.summary.exchange_can_trade is True
    assert payload.summary.app_live_execution_ready is False
    assert payload.summary.app_trading_paused is True
    assert payload.summary.app_operating_state == "PAUSED"
    assert payload.summary.app_pause_reason_code == "MANUAL_USER_REQUEST"
    assert payload.summary.guard_mode_reason_category == "pause"
    assert payload.summary.guard_mode_reason_code == "MANUAL_USER_REQUEST"
    assert payload.summary.guard_mode_reason_message == "운영자가 수동으로 거래를 중지해 가드 모드입니다."
    assert payload.summary.latest_blocked_reasons == ["TRADING_PAUSED"]
    assert payload.summary.available_balance == 930.25
    assert payload.summary.asset_count == 1
    assert payload.summary.open_positions == 1
    assert payload.summary.open_orders == 1
    assert len(payload.assets) == 1
    assert payload.assets[0].asset == "USDT"
    assert payload.positions[0].symbol == "BTCUSDT"
    assert payload.open_orders[0].reduce_only is True


def test_binance_account_snapshot_treats_missing_can_trade_as_not_explicitly_blocked(
    monkeypatch,
    db_session,
) -> None:
    settings_row = get_or_create_settings(db_session)
    settings_row.binance_api_key_encrypted = encrypt_secret("key", "change-me-local-dev-secret")
    settings_row.binance_api_secret_encrypted = encrypt_secret("secret", "change-me-local-dev-secret")
    db_session.add(settings_row)
    db_session.flush()

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_account_info(self):
            return {
                "totalWalletBalance": "1250.5",
                "availableBalance": "930.25",
                "totalUnrealizedProfit": "12.75",
                "totalMarginBalance": "1263.25",
                "totalPositionInitialMargin": "110.0",
                "totalOpenOrderInitialMargin": "35.5",
                "totalMaintMargin": "20.0",
                "assets": [
                    {
                        "asset": "USDT",
                        "walletBalance": "1250.5",
                        "availableBalance": "930.25",
                        "marginBalance": "1263.25",
                        "unrealizedProfit": "12.75",
                        "maxWithdrawAmount": "900.0",
                    }
                ],
            }

        def get_position_information(self):
            return []

        def get_open_orders(self):
            return []

    monkeypatch.setattr("trading_mvp.services.binance_account.BinanceClient", FakeClient)

    payload = get_binance_account_snapshot(db_session)

    assert payload.summary.connected is True
    assert payload.summary.can_trade is True
    assert payload.summary.exchange_can_trade is True
    assert "omitted canTrade" in payload.summary.message


def test_cached_binance_account_snapshot_falls_back_to_local_rows(db_session) -> None:
    settings_row = get_or_create_settings(db_session)
    db_session.add(settings_row)
    db_session.add(
        PnLSnapshot(
            snapshot_date=utcnow_naive().date(),
            equity=750.0,
            cash_balance=720.0,
            wallet_balance=740.0,
            available_balance=710.0,
            gross_realized_pnl=0.0,
            fee_total=0.0,
            funding_total=0.0,
            net_pnl=0.0,
            realized_pnl=0.0,
            unrealized_pnl=10.0,
            daily_pnl=0.0,
            cumulative_pnl=0.0,
            consecutive_losses=0,
        )
    )
    db_session.flush()

    payload = get_cached_binance_account_snapshot(db_session)

    assert payload["status"] == "empty"
    assert payload["source"] == "local"
    assert payload["payload"]["summary"]["total_wallet_balance"] == 740.0
    assert payload["payload"]["summary"]["available_balance"] == 710.0


def test_binance_account_cache_status_preserves_last_successful_payload(db_session) -> None:
    payload = BinanceAccountResponse(
        summary=BinanceAccountSummary(
            connected=True,
            message="live account ok",
            testnet_enabled=False,
            futures_enabled=True,
            can_trade=True,
            exchange_can_trade=True,
            total_wallet_balance=1250.0,
            available_balance=1100.0,
            total_unrealized_profit=15.0,
            total_margin_balance=1265.0,
            asset_count=1,
        ),
        assets=[
            BinanceAccountAsset(
                asset="USDT",
                wallet_balance=1250.0,
                available_balance=1100.0,
                margin_balance=1265.0,
                unrealized_profit=15.0,
            )
        ],
    )

    ready = store_binance_account_cache_result(db_session, payload, duration_ms=42.4)
    assert ready["status"] == "ready"
    assert ready["source"] == "cached_live"
    assert ready["duration_ms"] == 42.4
    assert ready["payload"]["summary"]["total_wallet_balance"] == 1250.0

    queued = mark_binance_account_refresh_requested(db_session)
    assert queued["status"] == "queued"
    assert queued["source"] == "cached_live"
    assert queued["payload"]["summary"]["total_wallet_balance"] == 1250.0

    failed = store_binance_account_cache_failure(db_session, "timeout", duration_ms=13000.0)
    assert failed["status"] == "failed"
    assert failed["last_error"] == "timeout"
    assert failed["payload"]["summary"]["total_wallet_balance"] == 1250.0


def test_binance_account_cache_abandons_stale_pending_status(db_session) -> None:
    payload = BinanceAccountResponse(
        summary=BinanceAccountSummary(
            connected=True,
            message="live account ok",
            testnet_enabled=False,
            futures_enabled=True,
            can_trade=True,
            exchange_can_trade=True,
            total_wallet_balance=1250.0,
            available_balance=1100.0,
            total_unrealized_profit=15.0,
            total_margin_balance=1265.0,
            asset_count=1,
        )
    )
    store_binance_account_cache_result(db_session, payload, duration_ms=42.4)
    settings_row = get_or_create_settings(db_session)
    detail = dict(settings_row.pause_reason_detail or {})
    cache = dict(detail["binance_account_cache"])
    cache.update(
        {
            "status": "refreshing",
            "started_at": (utcnow_naive() - timedelta(minutes=30)).isoformat(),
            "message": "Binance 원본 계정 캐시를 갱신 중입니다.",
        }
    )
    detail["binance_account_cache"] = cache
    settings_row.pause_reason_detail = detail
    db_session.add(settings_row)
    db_session.flush()

    response = get_cached_binance_account_snapshot(db_session)

    assert response["status"] == "ready"
    assert response["source"] == "cached_live"
    assert response["last_error"] == "ACCOUNT_CACHE_REFRESH_ABANDONED"
    assert response["payload"]["summary"]["total_wallet_balance"] == 1250.0

    renewed = mark_binance_account_refresh_requested(db_session)

    assert renewed["status"] == "queued"
    assert renewed["source"] == "cached_live"
    assert renewed["started_at"] is None
    assert renewed["last_error"] is None
    assert renewed["payload"]["summary"]["total_wallet_balance"] == 1250.0
