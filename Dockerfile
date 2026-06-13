FROM ubuntu:24.04 AS base

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-venv \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt
RUN python3 -m playwright install --with-deps chromium

FROM base

COPY . /app

RUN mkdir -p /app/data /app/logs /app/exports/csv /app/exports/xlsx && \
    chmod +x /app/entrypoint.sh

ENTRYPOINT ["./entrypoint.sh"]
