# syntax=docker/dockerfile:1-labs
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml README.md alembic.ini ./
COPY --parents backend/** ./
COPY --parents alembic/** ./
COPY --parents workers/** ./
COPY --parents scripts/** ./
COPY --parents prompts/** ./
COPY --parents docs/** ./
COPY --parents schemas/** ./

RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -e .

CMD ["python", "-m", "uvicorn", "trading_mvp.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]
