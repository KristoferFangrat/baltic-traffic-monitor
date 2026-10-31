FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY sql/ sql/
COPY src/ src/
COPY dashboard/ dashboard/

# Run as an unprivileged user
RUN useradd --create-home --uid 10001 app
USER app

# Default command; docker-compose overrides it per service
CMD ["python", "-m", "btm.collector", "run"]
