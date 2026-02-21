FROM python:3.12-slim AS base

RUN pip install uv

WORKDIR /app

COPY pyproject.toml .
COPY app/ app/

RUN uv venv /app/.venv && /app/.venv/bin/pip install .

ENV PATH="/app/.venv/bin:$PATH"

CMD ["python", "-m", "app.server"]
