FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY app/ app/

RUN pip install --no-cache-dir .

# M15: Run as non-root user
RUN adduser --disabled-password --gecos "" appuser
USER appuser

CMD ["python", "-m", "app.server"]
