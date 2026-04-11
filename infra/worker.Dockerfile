FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml README.md alembic.ini ./
COPY backend ./backend
COPY alembic ./alembic
COPY workers ./workers
COPY scripts ./scripts
COPY prompts ./prompts
COPY docs ./docs
COPY schemas ./schemas

RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -e .

CMD ["python", "workers/worker.py"]

