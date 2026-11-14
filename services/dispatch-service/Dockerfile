# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Зависимости ставятся отдельным слоем, чтобы кэш сборки
# не сбрасывался при каждом изменении исходного кода
COPY services/dispatch-service/requirements.txt .
RUN pip install -r requirements.txt

COPY common/taxi_common ./taxi_common
COPY services/dispatch-service/dispatch_service ./dispatch_service

# Процесс в контейнере работает не от root
RUN useradd --system --uid 10001 app
USER app

# Порт 8000 — только метрики Prometheus
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/metrics', timeout=2)"

CMD ["python", "-m", "dispatch_service.main"]
