from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trading_mvp.time_utils import utcnow_naive

PENDING_ENTRY_TIME_BASIS = "app_utc_naive"
PENDING_ENTRY_DB_UTC_NOW_EXPRESSION = "timezone('UTC', now())"
PENDING_ENTRY_EXPIRED_SMOKE_SQL = f"""
select
    id,
    symbol,
    side,
    plan_status,
    expires_at,
    {PENDING_ENTRY_DB_UTC_NOW_EXPRESSION} as app_utc_now,
    expires_at < {PENDING_ENTRY_DB_UTC_NOW_EXPRESSION} as expired_by_app_utc_now,
    extract(epoch from (expires_at - {PENDING_ENTRY_DB_UTC_NOW_EXPRESSION}))::bigint
        as remaining_ttl_seconds
from pending_entry_plans
where plan_status = 'armed'
order by expires_at asc, id asc
""".strip()


def pending_entry_utc_naive(value: datetime | None = None) -> datetime:
    if value is None:
        return utcnow_naive()
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def pending_entry_expires_at(created_at: datetime, ttl_minutes: int | None) -> datetime:
    base = pending_entry_utc_naive(created_at)
    return base + timedelta(minutes=max(int(ttl_minutes or 15), 1))


def pending_entry_plan_is_expired(
    expires_at: datetime | None,
    *,
    now: datetime | None = None,
    inclusive: bool = True,
) -> bool:
    if expires_at is None:
        return False
    normalized_expires_at = pending_entry_utc_naive(expires_at)
    normalized_now = pending_entry_utc_naive(now)
    if inclusive:
        return normalized_expires_at <= normalized_now
    return normalized_expires_at < normalized_now


def pending_entry_remaining_ttl_seconds(
    expires_at: datetime | None,
    *,
    now: datetime | None = None,
) -> int | None:
    if expires_at is None:
        return None
    normalized_expires_at = pending_entry_utc_naive(expires_at)
    normalized_now = pending_entry_utc_naive(now)
    return int((normalized_expires_at - normalized_now).total_seconds())


def pending_entry_expiry_context(
    expires_at: datetime | None,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    app_utc_now = pending_entry_utc_naive(now)
    remaining_ttl_seconds = pending_entry_remaining_ttl_seconds(expires_at, now=app_utc_now)
    return {
        "expires_at_time_basis": PENDING_ENTRY_TIME_BASIS,
        "app_utc_now": app_utc_now,
        "remaining_ttl_seconds": remaining_ttl_seconds,
        "expired_by_app_utc_now": (
            remaining_ttl_seconds is not None and remaining_ttl_seconds <= 0
        ),
    }
