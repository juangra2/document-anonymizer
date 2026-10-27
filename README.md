# Anonimizador de documentos

Servicio que anonimiza de forma **irreversible** documentos PDF, imágenes (JPEG, PNG, TIFF) y
documentos de Word (.docx) de la administración pública (Ajuntament de València), en castellano y
valenciano. Detecta los datos personales —nombres, DNI y NIE, direcciones, teléfonos, cuentas bancarias, fechas de nacimiento y una treintena de tipos más—,
los **borra del documento** (el texto tachado desaparece del PDF, no queda debajo de un recuadro) y
entrega un PDF sin metadatos ni contenido oculto, junto con un informe de lo que se ha hecho y de
lo que conviene revisar a mano. Lo ha desarrollado la Universitat de València (IDAL) para el
Ajuntament de València.

Admite documentos digitales, escaneados o mezclados, desde una página hasta expedientes de
cientos. Se usa desde una **página web** propia o desde su **API**.

Qué hace, cómo lo hace y cómo sabemos que funciona: [memoria](docs/memoria.md). El detalle para
mantener el código: [referencia técnica](docs/referencia_tecnica.md). Las cifras de calidad, para
quien no ha seguido el trabajo: [`evaluation/informe_final.html`](evaluation/informe_final.html).
**Licencia:** GNU Affero General Public License v3.0 o posterior (AGPL-3.0-or-later); el texto está
en [`LICENSE`](LICENSE). Las licencias de los componentes de terceros (librerías, modelos y datos
del INE), y lo que obligan: [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md). Los dos viajan
también dentro de la imagen, en `/app`.

---

## Índice

1. [Despliegue con Docker](#despliegue-con-docker) — paso a paso, para el servidor
2. [Configuración](#configuración) — las variables que se pueden cambiar
3. [Comprobar que funciona y ver el registro](#comprobar-que-funciona-y-ver-el-registro)
4. [Instalación en local, sin Docker](#instalación-en-local-sin-docker) — para desarrollo
5. [Preparar la imagen](#preparar-la-imagen) — para quien la construye y la envía

---

## Despliegue con Docker

Esta sección se entiende sola: es lo que necesita quien recibe la imagen como fichero, sin el
repositorio.

### Qué es Docker, en dos frases

Una **imagen** es un paquete que lleva dentro todo lo que el anonimizador necesita para
funcionar: el programa, las librerías y los modelos de inteligencia artificial. Un **contenedor**
es esa imagen en marcha; se arranca y se para con una orden, y no instala nada en el servidor fuera
de Docker.

La imagen del anonimizador **no necesita internet**: los modelos van dentro y no descarga nada al
arrancar.

### Qué tiene que tener el servidor

| Qué | Para qué | Cómo comprobarlo |
|---|---|---|
| Linux de 64 bits con una GPU NVIDIA | El OCR y la detección van en la tarjeta; sin ella funciona, pero mucho más despacio | `nvidia-smi` lista las tarjetas |
| **Driver de NVIDIA 570 o posterior** | La imagen usa CUDA 12.8, que necesita al menos ese driver | `nvidia-smi`, arriba a la derecha: «Driver Version» |
| Docker Engine y su complemento `compose` | Cargar y arrancar la imagen | `docker version` y `docker compose version` |
| **nvidia-container-toolkit** | Que los contenedores puedan usar la GPU | `nvidia-ctk --version`. Tras instalarlo: `sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker` |
| Unos 40 GB libres en disco | La imagen cargada (unos 11 GB; hasta 18 GB si Docker guarda también las capas comprimidas), el fichero recibido (6,6 GB) y los documentos en proceso | `df -h` |
| **Disco cifrado** donde Docker guarda sus volúmenes (normalmente `/var/lib/docker`) | Los documentos en proceso se escriben en disco; lo borrado puede dejar rastro físico. **Requisito** | Lo confirma el equipo de sistemas |

La instalación de nvidia-container-toolkit está en
<https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html>.

### Qué se recibe

- `anonimizador-1.0.tar.gz`: la imagen, comprimida (6,6 GB).
- `anonimizador-1.0.tar.gz.sha256`: su huella, para comprobar que ha llegado entera.
- `docker-compose.yml`: el fichero de arranque (su contenido está también [más abajo](#4-arrancar)).

### 1. Comprobar que el fichero ha llegado entero

En la carpeta donde están los dos ficheros:

```bash
sha256sum -c anonimizador-1.0.tar.gz.sha256
```

Tiene que responder `anonimizador-1.0.tar.gz: OK`. Si dice `FAILED`, el fichero se ha estropeado
por el camino: hay que volver a pedirlo, no cargarlo.

### 2. Cargar la imagen en Docker

```bash
gunzip -c anonimizador-1.0.tar.gz | docker load
```

Tarda unos minutos (seis y medio en un portátil) y termina con `Loaded image: anonimizador:1.0`. Para verla:

```bash
docker images anonimizador
```

### 3. Comprobar que Docker ve la GPU

```bash
docker run --rm --gpus device=0 --entrypoint nvidia-smi anonimizador:1.0
```

Tiene que salir la tabla de `nvidia-smi` con **una** tarjeta. Si da un error del tipo
`could not select device driver`, falta nvidia-container-toolkit o no se ha reiniciado Docker
tras configurarlo.

### 4. Arrancar

Se arranca con el fichero `docker-compose.yml`, que deja escrito cómo se ejecuta para no tener que
recordarlo. En una carpeta propia del servicio (por ejemplo `/opt/anonimizador`), con este
contenido:

```yaml
name: anonimizador

services:
  anonimizador:
    image: anonimizador:1.0
    container_name: anonimizador
    restart: unless-stopped
    ports:
      - "${HOST_PORT:-8000}:8000"
    environment:
      MAX_UPLOAD_MB: ${MAX_UPLOAD_MB:-200}
      MAX_PDF_PAGES: ${MAX_PDF_PAGES:-300}
      RESULT_TTL_MINUTES: ${RESULT_TTL_MINUTES:-360}
      JOB_QUEUE_MAX: ${JOB_QUEUE_MAX:-20}
      MAX_CONCURRENT_DOCUMENTS: ${MAX_CONCURRENT_DOCUMENTS:-1}
      LOG_PII: ${LOG_PII:-false}
      OCR_USE_GPU: ${OCR_USE_GPU:-true}
      NER_USE_GPU: ${NER_USE_GPU:-false}
      ROOT_PATH: ${ROOT_PATH:-}
    volumes:
      - trabajos:/data/trabajos
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              device_ids: ["${GPU_DEVICE:-0}"]
              capabilities: [gpu]

volumes:
  trabajos:
```

Los valores que se quieran cambiar se ponen en un fichero `.env` en la misma carpeta, una variable
por línea (ver [Configuración](#configuración)). Por ejemplo, para usar la segunda tarjeta y el
puerto 8080:

```
GPU_DEVICE=1
HOST_PORT=8080
```

Y se arranca:

```bash
cd /opt/anonimizador
docker compose up -d
```

`-d` lo deja funcionando en segundo plano. El contenedor se reinicia solo si se cae o si se
reinicia el servidor (`restart: unless-stopped`), salvo que se haya parado a mano.

**Una sola GPU.** `GPU_DEVICE` elige **una** tarjeta por su número (el de `nvidia-smi`, desde 0).
En un servidor compartido con varias tarjetas, nunca se le dan todas.

Para pararlo: `docker compose down`. Para volver a arrancarlo: `docker compose up -d`.

### 5. Abrirlo

En el navegador: `http://<servidor>:8000` (o el puerto de `HOST_PORT`). La documentación de la API
está en `http://<servidor>:8000/docs`.

**El servicio no tiene usuarios ni contraseñas**, por decisión del proyecto: tiene que estar en una
red a la que solo llegue quien deba usarlo, o detrás de un proxy inverso que ponga la
autenticación. Detrás de un proxy con un prefijo de ruta (`https://intranet/anonimizador/`), ese
prefijo va en `ROOT_PATH`.

### Carpeta de trabajos

Los documentos se guardan mientras se procesan y hasta que caducan (`RESULT_TTL_MINUTES`, seis
horas por defecto) en `/data/trabajos` dentro del contenedor. Se borran solos al caducar, y al
arrancar se borra lo que hubiera quedado de una ejecución anterior.

- **Por defecto** es un volumen de Docker (`trabajos`). Docker lo crea con los permisos correctos y
  no hay que hacer nada.
- **Si se prefiere una carpeta concreta del servidor** (por ejemplo en otro disco), se cambia la
  línea de `volumes` por `- /srv/anonimizador/trabajos:/data/trabajos` y **antes de arrancar** se
  prepara así:

  ```bash
  sudo mkdir -p /srv/anonimizador/trabajos
  sudo chown 10001:10001 /srv/anonimizador/trabajos
  sudo chmod 700 /srv/anonimizador/trabajos
  ```

  El servicio no corre como administrador (root) dentro del contenedor, sino con el usuario 10001,
  y necesita poder escribir ahí. Si no puede, **no arranca** y el registro dice exactamente esto
  mismo: `No se puede escribir en la carpeta de trabajos /data/trabajos…`.

En los dos casos, esa carpeta tiene que estar en un **disco cifrado**.

### Sin GPU

Si el contenedor arranca sin tarjeta, funciona en CPU, mucho más despacio. Lo dice el registro al
arrancar (`*** SIN GPU ***`) y `/health/ready` (`"gpu": {"available": false}` y un aviso en
`issues`). Para arrancarlo así a propósito, sin compose:

```bash
docker run -d --name anonimizador -p 8000:8000 -v trabajos:/data/trabajos anonimizador:1.0
```

Los documentos digitales tardan lo mismo que con GPU (unos segundos). **Los escaneados no
sirven así**: en la prueba, cada página tardaba más de los 360 s que se le dan al OCR
(`OCR_PAGE_TIMEOUT_S`) y salía **tapada entera**, sin que se filtrara nada. Si hubiera que usarlo
en CPU, hay que subir ese límite, por ejemplo con `-e OCR_PAGE_TIMEOUT_S=3600`, y contar con
cuartos de hora por página.

### Actualizar a una versión nueva

Se carga la imagen nueva (pasos 1 y 2), se cambia `image:` en `docker-compose.yml` y
`docker compose up -d`. Los trabajos en curso se pierden al reiniciar: mejor hacerlo sin documentos
en proceso.

---

## Configuración

Todo se configura con **variables de entorno**: en el fichero `.env` junto a `docker-compose.yml`,
o en `.env` en la raíz del proyecto si se ejecuta sin Docker (plantilla en `.env.example`).

**Del despliegue con Docker** (las lee `docker-compose.yml`):

| Variable | Por defecto | Para qué |
|---|---|---|
| `GPU_DEVICE` | `0` | Qué tarjeta usa, por su número en `nvidia-smi`. Una sola |
| `HOST_PORT` | `8000` | Puerto del servidor donde se publica el servicio |

**Del servicio:**

| Variable | Por defecto | Para qué |
|---|---|---|
| `MAX_UPLOAD_MB` | `200` | Tamaño máximo de un documento |
| `MAX_PDF_PAGES` | `300` | Páginas máximas por documento |
| `MAX_CONCURRENT_DOCUMENTS` | `1` | Documentos procesados a la vez. Es el límite de la memoria de la GPU: no subirlo con una tarjeta de 8 GB |
| `JOB_QUEUE_MAX` | `20` | Documentos en cola antes de responder «servicio ocupado» |
| `RESULT_TTL_MINUTES` | `360` | Minutos que se conserva el resultado antes de borrarlo (6 h) |
| `JOB_SWEEP_INTERVAL_S` | `60` | Cada cuánto se borran los trabajos caducados |
| `STORE_ORIGINAL` | `false` | Conservar el original tras procesarlo |
| `ROOT_PATH` | *(vacío)* | Prefijo de ruta detrás de un proxy inverso |
| `TEMP_ROOT` | `/data/trabajos` en Docker, `/tmp/anonymizer` fuera | Carpeta de trabajos |
| `LOG_PII` | `false` | Escribir texto de los documentos en el registro, para depurar. **Nunca en producción** |
| `OCR_USE_GPU` | `true` | OCR en la GPU si la hay; sin GPU, en CPU de todas formas |
| `NER_USE_GPU` | `false` en Docker | Detección de nombres (spaCy) en GPU. Necesitaría CuPy, que no va en la imagen; en CPU es como se ha medido la calidad |
| `OCR_ENGINE` | `auto` | `auto` (MinerU y, si falta, EasyOCR), `mineru` o `easyocr` |
| `OCR_MINERU_MODEL_PATH` | `/opt/modelos/mineru` en Docker | Pesos de MinerU |
| `EASYOCR_MODEL_PATH` | `/opt/modelos/easyocr` en Docker | Pesos de EasyOCR |
| `EASYOCR_ALLOW_DOWNLOAD` | `false` en Docker | Permitir que EasyOCR descargue pesos. En el servidor, siempre `false` |
| `OCR_PDF_RENDER_DPI` | `200` | Resolución a la que se leen las páginas escaneadas |
| `OCR_PAGE_TIMEOUT_S` | `360` | Tiempo máximo de OCR por página; la que lo agota se tacha entera |
| `REDACT_UNREADABLE_PAGES` | `true` | Tachar entera la página que no se puede leer |
| `OCR_AUTO_ROTATE_ENABLED` | `true` | Corregir escaneos girados |
| `PRESIDIO_SCORE_THRESHOLD` | `0.55` | Puntuación mínima de una detección |
| `FULL_REDACTION_ENABLED` | `true` | Tachar entera la copia de un DNI o una autoliquidación |
| `PLAN_PAGE_ENABLED` | `true` | Tratar los planos de obra por su cajetín |
| `VERIFY_OCR_LAYER_ENABLED` | `false` | Verificación final también por OCR (duplica el tiempo) |

El resto de umbrales están documentados junto a su definición en `app/core/config.py`.

---

## Comprobar que funciona y ver el registro

**Estado del servicio:**

```bash
curl http://localhost:8000/health/ready
```

Responde `200` si está listo y `503` si le falta algo esencial. Dice si hay GPU (`gpu`), si están
los modelos (`models`), si el trabajador que procesa la cola está vivo (`queue`) y qué problemas ve
(`issues`). Docker lo pregunta solo cada 30 segundos: `docker ps` muestra el contenedor como
`healthy` o `unhealthy`.

**Registro:**

```bash
docker compose logs -f          # en directo; Ctrl+C para salir
docker compose logs --since 1h  # la última hora
```

Al arrancar tiene que aparecer `GPU en uso: NVIDIA …`. Avisos que conviene conocer:

| En el registro | Qué significa |
|---|---|
| `*** SIN GPU ***` | Funciona en CPU, despacio. Revisar `GPU_DEVICE`, el driver y nvidia-container-toolkit |
| `No se puede escribir en la carpeta de trabajos …` | Permisos de la carpeta montada (ver [Carpeta de trabajos](#carpeta-de-trabajos)) |
| `LOG_PII ESTA ACTIVADO` | El registro está guardando texto de los documentos: desactivarlo |

El registro **no lleva texto de los documentos** ni nombres de fichero.

**Prueba completa:** abrir la página en el navegador, subir un PDF y descargar el resultado.

---

## Instalación en local, sin Docker

Para desarrollo. Requiere **Python 3.12** y, para el OCR, una GPU NVIDIA con CUDA 12.8 (driver 570
o posterior). Sin GPU funciona en CPU, pero los escaneados son muy lentos.

```bash
python -m venv .venv
source .venv/bin/activate                 # Windows: .venv\Scripts\activate

# PyTorch con CUDA 12.8 primero: la misma versión que en la imagen
pip install torch==2.11.0 torchvision==0.26.0 --index-url https://download.pytorch.org/whl/cu128

pip install -e ".[test,mineru]"
python -m spacy download es_core_news_md

# Pesos de MinerU (≈2,2 GB)
hf download opendatalab/MinerU2.5-Pro-2604-1.2B --local-dir data/model/MinerU2.5-Pro-2604-1.2B

cp .env.example .env
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

La página queda en <http://127.0.0.1:8000>. EasyOCR descarga sus pesos en el primer uso si
`EASYOCR_ALLOW_DOWNLOAD=true`.

**Tests** (unos 15 minutos con los lentos; no necesitan el servicio arrancado):

```bash
python -m pytest -q                 # todo
python -m pytest -q -m "not lento"  # sin los que pasan documentos enteros por el OCR (~5 min)
```

Los tests de metadatos con ExifTool y qpdf se saltan si esas herramientas no están instaladas.

---

## Preparar la imagen

Para quien la construye y la envía. Hace falta el repositorio con los pesos de los modelos en
`data/model/`: MinerU en `data/model/MinerU2.5-Pro-2604-1.2B` (ver arriba) y EasyOCR en `data/model/easyocr`
(`craft_mlt_25k.pth` y `latin_g2.pth`, que EasyOCR deja en `~/.EasyOCR/model` la primera vez).

```bash
docker build -f docker/Dockerfile -t anonimizador:1.0 .
docker save anonimizador:1.0 | gzip > anonimizador-1.0.tar.gz
sha256sum anonimizador-1.0.tar.gz > anonimizador-1.0.tar.gz.sha256
```

Se ejecuta desde la raíz del repositorio, que es el contexto de la construcción; todo lo de Docker
está en `docker/`. Se envían esos dos ficheros y `docker/docker-compose.yml`. Para probarla en el
propio equipo: `docker compose -f docker/docker-compose.yml up -d`.

`docker save` ya guarda las capas comprimidas: el `.tar` sale de 6,6 GB y gzip apenas lo reduce. Se
comprime igualmente para que el nombre y la orden de carga sean siempre los mismos.

Qué lleva la imagen y por qué (versiones de CUDA y torch, base, dependencias, modelos, usuario sin
privilegios): [referencia técnica](docs/referencia_tecnica.md#despliegue).
