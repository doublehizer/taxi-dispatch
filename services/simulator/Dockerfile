# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY services/simulator/requirements.txt .
RUN pip install -r requirements.txt

COPY services/simulator/simulator.py .

RUN useradd --system --uid 10001 app
USER app

CMD ["python", "simulator.py"]
