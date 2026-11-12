# AutoGrade backend: one always-on container.
# Run exactly one instance with one worker: the marking queue and sign-in sessions live in this process.
FROM python:3.14-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libglib2.0-0 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Queued sheets and the usage log go on a persistent disk mounted at /data.
ENV QUEUE_DATA_DIR=/data/queue_data \
    GEMINI_USAGE_DIR=/data/gemini_usage \
    PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
