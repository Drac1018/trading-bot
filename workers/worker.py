from __future__ import annotations

import sys
import time

from redis import Redis
from trading_mvp.config import get_settings


def main() -> None:
    settings = get_settings()
    if sys.platform == "win32":
        while True:
            time.sleep(60)
    try:
        from rq import Connection, Queue, Worker  # type: ignore[import-not-found]
    except Exception:
        while True:
            time.sleep(60)

    connection = Redis.from_url(settings.redis_url)
    trading_queue = Queue("trading", connection=connection)
    review_queue = Queue("reviews", connection=connection)
    with Connection(connection):
        worker = Worker([trading_queue, review_queue])
        worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
