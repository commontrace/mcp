FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY app/ app/

RUN pip install --no-cache-dir "fastmcp>=3.0.0" "httpx>=0.27" "pydantic-settings>=2.0.0" "structlog>=24.0" && \
    pip install --no-cache-dir .

CMD ["python", "-m", "app.server"]
