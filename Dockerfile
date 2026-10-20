# Imagen del anonimizador: lleva dentro el codigo, las librerias y los modelos,
# para que funcione en un servidor sin salida a internet.  Se construye con
#     docker build -t anonimizador:1.0 .
# y se transporta como fichero (ver README, "Despliegue con Docker").
#
# CUDA y torch.  torch 2.11.0 para CUDA 12.8, la combinacion con la que se ha
# medido la calidad (c4).  CUDA 12.8 necesita un driver de NVIDIA 570 o
# posterior en el servidor: lo cumplen el equipo de desarrollo (572.61) y el
# servidor de la empresa (580.173).  Un driver nuevo ejecuta CUDA mas antiguas,
# no al reves: con CUDA 13 la imagen no arrancaria en drivers de la serie 570.
#
# Base python:3.12-slim y no nvidia/cuda: el paquete de torch para cu128 ya trae
# las librerias de CUDA y cuDNN, y el driver lo pone el servidor a traves de
# nvidia-container-toolkit.  Una base nvidia/cuda duplicaria unos 3 GB.
FROM python:3.12.6-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Librerias del sistema: zbar para pyzbar (codigos QR y de barras), glib y
# OpenMP para OpenCV, scikit-image y torch.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libzbar0 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# torch con CUDA 12.8, fijado.
RUN pip install torch==2.11.0 torchvision==0.26.0 \
        --index-url https://download.pytorch.org/whl/cu128

# El resto de librerias, con las versiones exactas del entorno medido.
COPY docker/requirements.txt /tmp/requirements.txt
RUN pip install --no-deps -r /tmp/requirements.txt \
    && pip install --no-deps \
        "https://github.com/explosion/spacy-models/releases/download/es_core_news_md-3.8.0/es_core_news_md-3.8.0-py3-none-any.whl" \
    && rm /tmp/requirements.txt

# Modelos dentro de la imagen, de solo lectura para el servicio.
COPY model/MinerU2.5-Pro-2604-1.2B /opt/modelos/mineru
COPY model/easyocr /opt/modelos/easyocr

WORKDIR /app
COPY app /app/app

# El servicio no corre como root: usuario sin privilegios con UID fijo (10001),
# dueno solo de la carpeta de trabajos.  Un volumen con nombre de Docker hereda
# este dueno al crearse; una carpeta del servidor montada encima, no (ver README).
RUN useradd --uid 10001 --no-create-home --home-dir /tmp/anonimizador \
        --shell /usr/sbin/nologin anonimizador \
    && mkdir -p /data/trabajos /tmp/anonimizador \
    && chown 10001:10001 /data/trabajos /tmp/anonimizador \
    && chmod 700 /data/trabajos

# Configuracion por defecto del contenedor.  Todo se puede cambiar con
# variables de entorno al arrancar (ver README).
# NER_USE_GPU=false: spaCy solo usaria la GPU con CuPy, que no va en la imagen;
# en CPU es como se midio la calidad, y asi el registro no avisa en cada
# documento.  El OCR, que es lo pesado, si va en la GPU.
ENV HOME=/tmp/anonimizador \
    TEMP_ROOT=/data/trabajos \
    OCR_MINERU_MODEL_PATH=/opt/modelos/mineru \
    EASYOCR_MODEL_PATH=/opt/modelos/easyocr \
    EASYOCR_ALLOW_DOWNLOAD=false \
    NER_USE_GPU=false \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_DATASETS_OFFLINE=1 \
    ENVIRONMENT=docker

VOLUME /data/trabajos
USER 10001:10001
EXPOSE 8000

# Docker pregunta cada 30 s a /health/ready; si falla tres veces seguidas marca
# el contenedor como "unhealthy".  Los dos primeros minutos no cuentan (arranque).
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=8)"]

# --proxy-headers: si delante hay un proxy inverso con HTTPS, que los enlaces
# salgan con https.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
