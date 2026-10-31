# Build:  docker build -t beacon-telemetry .
# Run:    docker run -p 8000:8000 -v beacon-data:/data beacon-telemetry
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first. Docker caches this layer, so rebuilds after
# a code change are fast unless requirements.txt itself changes.
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Then copy the code (the folder layout matches the repository).
COPY backend/ backend/
COPY dashboard/ dashboard/

# Run as a normal user, not root. The database lives in /data so it can be a volume.
RUN useradd --create-home appuser && mkdir /data && chown appuser /data
USER appuser
ENV BEACON_DB=/data/telemetry.db

WORKDIR /app/backend
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# 0.0.0.0 so the server is reachable from outside the container
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
