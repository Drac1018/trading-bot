from __future__ import annotations

from datetime import UTC, datetime


def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def utcnow_aware() -> datetime:
    return datetime.now(UTC)


def ensure_utc_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse_utc_datetime(value: object) -> datetime | None:
    if value in {None, ""}:
        return None
    if isinstance(value, datetime):
        return ensure_utc_aware(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        return ensure_utc_aware(parsed)
    return None


def isoformat_utc(value: datetime | None) -> str | None:
    normalized = ensure_utc_aware(value)
    if normalized is None:
        return None
    return normalized.isoformat()
