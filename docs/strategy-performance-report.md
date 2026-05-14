# Strategy Performance Report

Read-only CLI report for closed trade performance by stored trade tags.

Example:

```powershell
python scripts/strategy_performance_report.py --format table
```

Useful filters:

```powershell
python scripts/strategy_performance_report.py --strategy-id range_mean_reversion_engine --symbol BTCUSDT
python scripts/strategy_performance_report.py --strategy-id trend_pullback_engine --direction short --format csv --output tmp/trend_short.csv
python scripts/strategy_performance_report.py --risk-mode drawdown_recovery --format json
```

Default grouping is:

```text
strategy_id,regime_id,confirmation_type,risk_mode,symbol,direction
```

The script reads closed `positions` and linked `orders`, `executions`, and audit metadata. It does not modify trading runtime state, strategy thresholds, risk policy, or order execution behavior.
