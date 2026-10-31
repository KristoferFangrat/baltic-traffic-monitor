FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Pull in Debian security fixes released after the base image was built,
# and upgrade pip itself (an outdated pip is a common scanner finding)
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --upgrade pip

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY data/ data/
COPY sql/ sql/
COPY src/ src/
COPY dashboard/ dashboard/

# Run as an unprivileged user
RUN useradd --create-home --uid 10001 app
USER app

# Default command; docker-compose overrides it per service
CMD ["python", "-m", "btm.collector", "run"]
