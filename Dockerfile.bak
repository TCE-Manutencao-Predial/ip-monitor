FROM python:3.12-slim

LABEL maintainer="TCE Manutenção Predial"
LABEL app="ip-monitor"
LABEL version="1.0.0"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_NAME=ip-monitor \
    APP_PORT=5000 \
    CONFIG=docker

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN groupadd -g 1001 appuser && \
    useradd -m -u 1001 -g appuser appuser

RUN mkdir -p /var/softwaresTCE/ip-monitor/dados \
             /var/softwaresTCE/ip-monitor/logs && \
    chown -R appuser:appuser /var/softwaresTCE /app

USER appuser

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:5000/ || exit 1

CMD ["waitress-serve", "--host=0.0.0.0", "--port=5000", "config:app"]

