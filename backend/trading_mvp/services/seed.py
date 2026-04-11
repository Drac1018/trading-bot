from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from trading_mvp.models import User
from trading_mvp.services.settings import get_or_create_settings


def seed_demo_data(session: Session) -> dict[str, object]:
    get_or_create_settings(session)

    if session.scalar(select(User).limit(1)) is None:
        session.add(User(email="admin@local.dev", name="Local Admin", role="admin", is_active=True))

    session.commit()
    return {
        "status": "bootstrapped",
        "message": "라이브 전용 모드에서는 데모 거래/리플레이 데이터를 생성하지 않습니다.",
    }
