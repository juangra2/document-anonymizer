# Licencias de terceros

Componentes de terceros que lleva la imagen Docker del anonimizador (`anonimizador:1.0`), con su
licencia. La lista de paquetes de Python sale de los metadatos de los paquetes instalados en la
propia imagen (20-10-2026), no de memoria. Esto es un inventario técnico, no un dictamen jurídico.

## Resumen

- **El código propio del anonimizador** se publica bajo la **AGPL-3.0-or-later** (texto en
  [`LICENSE`](LICENSE)). Lo que sigue son los componentes de terceros que lleva.
- La gran mayoría de los paquetes tienen licencias libres permisivas (MIT, BSD, Apache-2.0, PSF,
  ISC): permiten el uso, la modificación y la redistribución conservando los avisos de copyright.
- **PyMuPDF** es **AGPL-3.0** (o licencia comercial de Artifex). Si el programa se ofrece a usuarios
  a través de una red, hay que poder ofrecerles su código fuente completo.
- **El modelo de spaCy `es_core_news_md`** es **GPL-3.0**. Distribuirlo (por ejemplo, entregar la
  imagen a otra entidad) obliga a acompañarlo de la licencia y dar acceso a sus fuentes.
- **python-bidi**, **libzbar0** y **libglib2.0** son **LGPL**: al distribuir, incluir la licencia y
  permitir sustituir la librería.
- **certifi** y **tqdm** llevan **MPL-2.0**: solo obliga sobre los cambios en sus propios ficheros.
- **Las librerías de CUDA de NVIDIA no son software libre**: su licencia permite usarlas y
  redistribuirlas con la aplicación, pero no modificarlas.

## Modelos y datos

| Componente | Origen | Licencia |
|---|---|---|
| MinerU 2.5-Pro (pesos `MinerU2.5-Pro-2604-1.2B`) | opendatalab, <https://huggingface.co/opendatalab/MinerU2.5-Pro-2604-1.2B> | Apache-2.0 (ficha del modelo) |
| mineru-vl-utils (cliente de MinerU) | opendatalab | Apache-2.0 |
| EasyOCR (código) | JaidedAI, <https://github.com/JaidedAI/EasyOCR> | Apache-2.0 |
| EasyOCR, pesos `craft_mlt_25k.pth` (detector CRAFT) y `latin_g2.pth` (reconocedor) | JaidedAI; CRAFT de NAVER Clova, <https://github.com/clovaai/CRAFT-pytorch> | **No confirmada** (ver abajo) |
| spaCy `es_core_news_md` 3.8.0 | Explosion, <https://spacy.io> | **GPL-3.0**. Entrenado con UD Spanish AnCora (GPL-3.0), WikiNER (CC BY 4.0), spaCy lookups (MIT) y vectores fastText (CC0) |
| Nombres, apellidos, municipios y provincias | Instituto Nacional de Estadística, <https://www.ine.es> | CC BY 4.0 (ver la cita obligatoria y la duda abajo) |

### Cita obligatoria del INE

Los ficheros `app/services/pii/gazetteers/ine_*` se han elaborado a partir de datos del INE. Según su
aviso legal, se debe citar así:

> Elaboración propia con datos extraídos del sitio web del INE: www.ine.es

Las condiciones piden además citar la fecha de la última actualización de los datos cuando el
original la indique, y no dar a entender que el INE participa en el uso o lo respalda.

## Sin confirmar

- **Pesos de EasyOCR.** El repositorio de EasyOCR es Apache-2.0 y el de CRAFT, MIT, pero ninguno
  declara expresamente la licencia de los pesos preentrenados (`craft_mlt_25k.pth`, `latin_g2.pth`).
- **Datos del INE.** El texto del aviso legal dice Creative Commons Reconocimiento 4.0 (CC BY 4.0),
  pero el pie de esa misma página dice CC BY-SA 4.0. Conviene confirmarlo con el INE.

## Fuera de la imagen

**ExifTool** y **qpdf** no van en la imagen ni los usa el servicio: solo se usan para verificar,
desde fuera, los metadatos de los PDF entregados (`evaluation/comprobar_metadatos.py`).

## Sistema

La imagen parte de `python:3.12.6-slim-bookworm`: Python 3.12 (licencia PSF) sobre Debian 12, con
119 paquetes de sistema bajo licencias libres diversas (GPL, LGPL, BSD y otras). Sus textos están
en la propia imagen, en `/usr/share/doc/*/copyright`, y las fuentes en <https://sources.debian.org>.
Los añadidos por el anonimizador:

| Paquete | Versión | Licencia |
|---|---|---|
| libzbar0 | 0.23.92-7+deb12u1 | **LGPL-2.1+** |
| libglib2.0-0 | 2.74.6-2+deb12u9 | **LGPL-2.1+** (con partes Expat y otras permisivas) |
| libgomp1 | 12.2.0-14+deb12u1 | GPL-3 con la excepción de biblioteca de GCC (no impone obligaciones al programa que la usa) |

## Paquetes de Python (180)

Licencia tal como la declara cada paquete (expresión SPDX, campo de licencia o clasificador).

| Paquete | Versión | Licencia |
|---|---|---|
| accelerate | 1.13.0 | Apache |
| aiofiles | 25.1.0 | Apache-2.0 |
| aiohappyeyeballs | 2.7.1 | PSF-2.0 |
| aiohttp | 3.14.3 | Apache-2.0 AND MIT |
| aiosignal | 1.4.0 | Apache 2.0 |
| annotated-doc | 0.0.4 | MIT |
| annotated-types | 0.7.0 | MIT License |
| anyio | 4.13.0 | MIT |
| arabic-reshaper | 3.0.1 | MIT |
| asn1crypto | 1.5.1 | MIT |
| attrs | 26.1.0 | MIT |
| av | 17.0.1 | BSD-3-Clause |
| beautifulsoup4 | 4.14.3 | MIT License |
| blis | 1.3.3 | BSD |
| boto3 | 1.43.9 | Apache-2.0 |
| botocore | 1.43.9 | Apache-2.0 |
| catalogue | 2.0.10 | MIT |
| certifi | 2026.4.22 | MPL-2.0 |
| cffi | 2.0.0 | MIT |
| charset-normalizer | 3.4.7 | MIT |
| click | 8.3.3 | BSD-3-Clause |
| cloudpathlib | 0.24.0 | MIT License |
| cobble | 0.1.4 | BSD License |
| colorlog | 6.10.1 | MIT License |
| confection | 1.3.3 | MIT |
| cryptography | 48.0.0 | Apache-2.0 OR BSD-3-Clause |
| cssselect2 | 0.10.1 | BSD License |
| cuda-bindings | 12.9.7 | Propietaria de NVIDIA |
| cuda-pathfinder | 1.6.0 | Apache-2.0 |
| cuda-toolkit | 12.8.1 | Metapaquete de NVIDIA, sin licencia declarada |
| cymem | 2.0.13 | MIT |
| distro | 1.9.0 | Apache License, Version 2.0 |
| easyocr | 1.7.2 | Apache License 2.0 |
| es_core_news_md | 3.8.0 | **GPL-3.0** |
| et_xmlfile | 2.0.0 | MIT |
| Faker | 26.3.0 | MIT License |
| fast-langdetect | 0.2.5 | MIT |
| fastapi | 0.136.1 | MIT |
| fasttext-predict | 0.9.2.4 | MIT |
| filelock | 3.29.0 | MIT |
| flatbuffers | 25.12.19 | Apache 2.0 |
| frozenlist | 1.8.0 | Apache-2.0 |
| fsspec | 2026.4.0 | BSD-3-Clause |
| h11 | 0.16.0 | MIT |
| hf-xet | 1.5.0 | Apache-2.0 |
| html5lib | 1.1 | MIT License |
| httpcore | 1.0.9 | BSD-3-Clause |
| httptools | 0.7.1 | MIT |
| httpx | 0.28.1 | BSD-3-Clause |
| httpx-retries | 0.5.0 | MIT License |
| huggingface_hub | 0.36.2 | Apache |
| idna | 3.13 | BSD-3-Clause |
| ImageIO | 2.37.3 | BSD-2-Clause |
| Jinja2 | 3.1.6 | BSD License |
| jiter | 0.14.0 | MIT |
| jmespath | 1.1.0 | MIT |
| json_repair | 0.59.10 | MIT |
| lazy-loader | 0.5 | BSD-3-Clause |
| loguru | 0.7.3 | MIT License |
| lxml | 6.1.0 | BSD-3-Clause |
| magika | 1.0.3 | Apache-2.0 |
| mammoth | 1.12.0 | BSD-2-Clause |
| markdown-it-py | 4.0.0 | MIT License |
| MarkupSafe | 3.0.3 | BSD-3-Clause |
| mdurl | 0.1.2 | MIT License |
| mineru_vl_utils | 0.2.7 | Apache-2.0 |
| modelscope | 1.37.0 | Apache-2.0 |
| mpmath | 1.3.0 | BSD |
| multidict | 6.9.1 | Apache License 2.0 |
| murmurhash | 1.0.15 | MIT |
| networkx | 3.6.1 | BSD-3-Clause |
| ninja | 1.13.0 | Apache Software License; BSD License |
| numpy | 2.4.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| nvidia-cublas-cu12 | 12.8.4.1 | Propietaria de NVIDIA |
| nvidia-cuda-cupti-cu12 | 12.8.90 | Propietaria de NVIDIA |
| nvidia-cuda-nvrtc-cu12 | 12.8.93 | Propietaria de NVIDIA |
| nvidia-cuda-runtime-cu12 | 12.8.90 | Propietaria de NVIDIA |
| nvidia-cudnn-cu12 | 9.19.0.56 | Propietaria de NVIDIA |
| nvidia-cufft-cu12 | 11.3.3.83 | Propietaria de NVIDIA |
| nvidia-cufile-cu12 | 1.13.1.3 | Propietaria de NVIDIA |
| nvidia-curand-cu12 | 10.3.9.90 | Propietaria de NVIDIA |
| nvidia-cusolver-cu12 | 11.7.3.90 | Propietaria de NVIDIA |
| nvidia-cusparse-cu12 | 12.5.8.93 | Propietaria de NVIDIA |
| nvidia-cusparselt-cu12 | 0.7.1 | Propietaria de NVIDIA |
| nvidia-nccl-cu12 | 2.28.9 | Propietaria de NVIDIA |
| nvidia-nvjitlink-cu12 | 12.8.93 | Propietaria de NVIDIA |
| nvidia-nvshmem-cu12 | 3.4.5 | Propietaria de NVIDIA |
| nvidia-nvtx-cu12 | 12.8.90 | Propietaria de NVIDIA |
| onnxruntime | 1.26.0 | MIT License |
| openai | 2.37.0 | Apache-2.0 |
| opencv-python-headless | 4.13.0.92 | Apache 2.0 |
| openpyxl | 3.1.5 | MIT |
| oscrypto | 1.3.0 | MIT |
| packaging | 26.2 | Apache-2.0 OR BSD-2-Clause |
| pandas | 2.3.3 | BSD 3-Clause License |
| pdfminer.six | 20260107 | MIT |
| pdftext | 0.6.3 | Apache-2.0 |
| phonenumbers | 9.0.29 | Apache-2.0 |
| pillow | 12.2.0 | MIT-CMU |
| pip | 24.2 | MIT |
| preshed | 3.0.13 | MIT |
| presidio_analyzer | 2.2.362 | MIT |
| propcache | 0.5.4 | Apache-2.0 |
| protobuf | 7.34.1 | 3-Clause BSD License |
| psutil | 7.2.2 | BSD-3-Clause |
| pyarrow | 24.0.0 | Apache-2.0 |
| pyclipper | 1.4.0 | MIT |
| pycparser | 3.0 | BSD-3-Clause |
| pydantic | 2.13.3 | MIT |
| pydantic-settings | 2.14.0 | MIT |
| pydantic_core | 2.46.3 | MIT |
| Pygments | 2.20.0 | BSD-2-Clause |
| pyHanko | 0.37.0 | MIT |
| pyhanko-certvalidator | 0.32.1 | MIT |
| pylatexenc | 2.10 | MIT |
| PyMuPDF | 1.27.2.3 | **AGPL-3.0** (o licencia comercial de Artifex) |
| pypdf | 6.11.0 | BSD-3-Clause |
| pypdfium2 | 4.30.0 | (Apache-2.0 OR BSD-3-Clause) AND LicenseRef-PdfiumThirdParty |
| pypptx-with-oxml | 1.0.3 | MIT |
| python-bidi | 0.6.7 | **LGPL** |
| python-dateutil | 2.9.0.post0 | Dual License |
| python-docx | 1.2.0 | MIT |
| python-dotenv | 1.2.2 | BSD-3-Clause |
| python-multipart | 0.0.27 | Apache-2.0 |
| pytz | 2026.2 | MIT |
| PyYAML | 6.0.3 | MIT |
| pyzbar | 0.1.9 | MIT |
| qrcode | 8.2 | BSD |
| qwen-vl-utils | 0.0.14 | Apache-2.0 |
| regex | 2026.4.4 | Apache-2.0 AND CNRI-Python |
| requests | 2.33.1 | Apache-2.0 |
| requests-file | 3.0.1 | Apache 2.0 |
| rich | 15.0.0 | MIT |
| robust-downloader | 0.0.2 | Apache Software License |
| s3transfer | 0.17.0 | Apache License 2.0 |
| safetensors | 0.7.0 | Apache Software License |
| scikit-image | 0.26.0 | BSD License |
| scipy | 1.17.1 | BSD License |
| setuptools | 81.0.0 | MIT |
| shapely | 2.1.2 | BSD 3-Clause |
| shellingham | 1.5.4 | ISC License |
| six | 1.17.0 | MIT |
| smart_open | 7.6.0 | MIT License |
| sniffio | 1.3.1 | MIT OR Apache-2.0 |
| soupsieve | 2.8.3 | MIT |
| spacy | 3.8.14 | MIT |
| spacy-huggingface-pipelines | 0.0.4 | MIT |
| spacy-legacy | 3.0.12 | MIT |
| spacy-loggers | 1.0.5 | MIT |
| srsly | 2.5.3 | MIT |
| starlette | 1.0.0 | BSD-3-Clause |
| sympy | 1.14.0 | BSD |
| thinc | 8.3.13 | MIT |
| tifffile | 2026.5.2 | BSD-3-Clause |
| tinycss2 | 1.5.1 | BSD License |
| tldextract | 5.3.1 | BSD-3-Clause |
| tokenizers | 0.22.2 | Apache Software License |
| torch | 2.11.0+cu128 | BSD-3-Clause |
| torchvision | 0.26.0+cu128 | BSD |
| tqdm | 4.67.3 | MPL-2.0 y MIT |
| transformers | 4.57.6 | Apache 2.0 License |
| triton | 3.6.0 | MIT License |
| typer | 0.25.1 | MIT |
| typing-inspection | 0.4.2 | MIT |
| typing_extensions | 4.15.0 | PSF-2.0 |
| tzdata | 2026.2 | Apache-2.0 |
| tzlocal | 5.4.4 | MIT |
| uritools | 6.1.3 | MIT |
| urllib3 | 2.6.3 | MIT |
| uvicorn | 0.46.0 | BSD-3-Clause |
| wasabi | 1.1.3 | MIT |
| watchfiles | 1.1.1 | MIT |
| weasel | 1.0.0 | MIT |
| webencodings | 0.6.1 | BSD License |
| websockets | 16.0 | BSD-3-Clause |
| wheel | 0.47.0 | MIT |
| wrapt | 2.1.2 | BSD-2-Clause |
| xlsxwriter | 3.2.9 | BSD-2-Clause |
| yarl | 1.25.1 | Apache-2.0 |
| zxing-cpp | 3.1.1 | Apache-2.0 |
