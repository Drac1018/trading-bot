from __future__ import annotations

from trading_mvp.models import RiskCheck
from trading_mvp.services.binance_account import get_binance_account_snapshot
from trading_mvp.services.secret_store import encrypt_secret
from trading_mvp.services.settings import get_or_create_settings


def test_binance_account_snapshot_returns_disconnected_without_credentials(db_session) -> None:
    payload = get_binance_account_snapshot(db_session)

    assert payload.summary.connected is False
    assert payload.assets == []
    assert payload.positions == []
    assert payload.open_orders == []


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
