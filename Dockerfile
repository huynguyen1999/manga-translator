# syntax=docker/dockerfile:1

# The official Python image has linux/amd64 and linux/arm64 variants. Docker
# Desktop runs this same Linux image on macOS.
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        build-essential \
        ffmpeg \
        gimp \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libvulkan1 \
        libxext6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY . .
RUN mkdir -p /app/models /app/workspace/batches /app/workspace/results /app/data/batches /app/data/results /app/result /app/upload-cache

EXPOSE 8000

ENTRYPOINT ["python", "server/main.py"]
CMD ["--host=0.0.0.0", "--port=8000", "--start-instance", "--no-gpu", "--workers=1"]
