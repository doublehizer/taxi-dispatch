# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Зависимости ставятся отдельным слоем, чтобы кэш сборки
# не сбрасывался при каждом изменении исходного кода
COPY services/driver-service/requirements.txt .
RUN pip install -r requirements.txt

COPY common/taxi_common ./taxi_common
COPY services/driver-service/driver_service ./driver_service

# Процесс в контейнере работает не от root
RUN useradd --system --uid 10001 app
USER app

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

CMD ["uvicorn", "driver_service.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
