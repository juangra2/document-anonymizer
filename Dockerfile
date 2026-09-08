FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app

RUN pip install --upgrade pip \
    && pip install -e . \
    && python -m spacy download es_core_news_md

RUN useradd -r -u 10001 appuser \
    && mkdir -p /tmp/anonymizer \
    && chown -R appuser:appuser /tmp/anonymizer /app

USER appuser

EXPOSE 8000

# --proxy-headers: el TLS lo termina el proxy inverso, no la aplicación; sin
# esto uvicorn no ve X-Forwarded-Proto y construye enlaces en http.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]