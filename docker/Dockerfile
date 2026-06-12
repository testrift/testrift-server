# syntax=docker/dockerfile:1

FROM python:3.12-slim AS base

# Avoid writing .pyc files and ensure stdout/stderr are unbuffered for logging.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install build/runtime dependencies first to leverage Docker layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy project metadata and source, then install the package.
COPY pyproject.toml VERSION README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .

# Container configuration: bind to all interfaces and use a persistent data volume.
COPY docker/testrift_server.yaml ./testrift_server.yaml

# Persistent storage for test run data and the SQLite database.
RUN mkdir -p /data
VOLUME ["/data"]

# Run as a non-root user.
RUN useradd --create-home --uid 10001 testrift \
    && chown -R testrift:testrift /app /data
USER testrift

EXPOSE 8080

CMD ["testrift-server"]
