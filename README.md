# document-anonymizer

API REST para la **anonimización irreversible de documentos PDF** en la administración pública española. Detecta y redacta información personal (PII) de forma física, eliminando también todos los metadatos del archivo resultante.

---

## Índice

1. [Qué hace](#qué-hace)
2. [Stack tecnológico](#stack-tecnológico)
3. [Arquitectura](#arquitectura)
4. [Instalación](#instalación)
5. [Uso rápido](#uso-rápido)
6. [Modos de redacción](#modos-de-redacción)
7. [Sistema de zonas layout-first](#sistema-de-zonas-layout-first)
8. [Motores OCR](#motores-ocr)
9. [Verificación post-redacción (4 capas)](#verificación-post-redacción-4-capas)
10. [Tipos de PII detectados](#tipos-de-pii-detectados)
11. [Endpoints de la API](#endpoints-de-la-api)
12. [Configuración](#configuración)
13. [Docker](#docker)

---

## Qué hace

`document-anonymizer` recibe un PDF (o imagen) y devuelve una versión con toda la información personal redactada de forma irreversible:

- **Redacción física**: las áreas se sobrescriben con rectángulos negros; no se puede recuperar el texto original inspeccionando el PDF.
- **Limpieza de metadatos**: elimina Author, Title, XMP, formularios AcroForm, archivos adjuntos, JavaScript y anotaciones.
- **Doble vía**: documentos digitales (con texto seleccionable) se procesan directamente con PyMuPDF; documentos escaneados (imágenes) se procesan con OCR.
- **Informe de auditoría**: cada petición devuelve un JSON con entidades detectadas, redactadas, residuales, score de riesgo y capas de verificación ejecutadas.
- **Soporte multilingüe**: español, valenciano y catalán.

### Lo que NO hace

- No almacena los documentos originales más allá del tiempo de procesamiento.
- No registra valores de PII en logs.
- No realiza anonimización reversible (no hay cifrado ni pseudonimización).

---

## Stack tecnológico

| Capa | Tecnología |
|------|-----------|
| Framework web | FastAPI 0.115 + Uvicorn |
| Modelos y validación | Pydantic 2.7 |
| Manipulación de PDF | PyMuPDF (fitz) 1.24 |
| Detección de PII | Microsoft Presidio 2.2 + spaCy 3.7 (`es_core_news_md`) |
| OCR principal | MinerU 2.5 (magic-pdf / mineru) |
| OCR fallback | EasyOCR |
| Visión por computador | OpenCV (firmas, QR) |
| Imagen | Pillow, NumPy |
| Python | 3.11+ |

---

## Arquitectura

El proyecto sigue una arquitectura **layout-first**: cada página se segmenta en zonas tipadas **antes** de ejecutar cualquier detector de PII.

```
Documento de entrada (PDF / imagen)
         │
         ▼
  ┌─────────────┐
  │  Inspector  │  Detecta si el PDF es digital, escaneado o híbrido
  └──────┬──────┘
         │
         ▼
  ┌─────────────────────────────────────────────────────┐
  │                  Segmentador de zonas               │
  │  DigitalPageSegmenter │ OcrPageSegmenter            │
  │                                                     │
  │  HEADER  │  FOOTER  │  FORM  │  TABLE  │  BODY      │
  │  SIGNATURE  │  QR_CODE  │  MARGINALIA                │
  └──────────────────────┬──────────────────────────────┘
                         │  ZonedPage
                         ▼
  ┌─────────────────────────────────────────────────────┐
  │              Redactor por zona                      │
  │  FORM/TABLE → layout_redact_rects (sin PII)         │
  │  SIGNATURE/QR → redacción total de zona             │
  │  BODY/HEADER/FOOTER → PiiAnalyzer con tipos         │
  │                       restringidos por zona         │
  └──────────────────────┬──────────────────────────────┘
                         │  PDF redactado
                         ▼
  ┌─────────────────────────────────────────────────────┐
  │         Verificación post-redacción (4 capas)       │
  │                                                     │
  │  1. Textual  → extracción texto + PII analyzer      │
  │  2. OCR      → renderizar + OCR + PII analyzer      │
  │  3. Metadata → Info dict, XMP, AcroForm, adjuntos   │
  │  4. Binary   → scan bytes crudos con regex          │
  │                                                     │
  │  risk_score por página → [0.0–1.0]                  │
  └──────────────────────┬──────────────────────────────┘
                         │
                         ▼
              PDF anonimizado + Informe JSON
```

### Estructura de directorios

```
app/
├── api/                    # Rutas FastAPI
│   ├── health.py
│   └── routes.py
├── core/                   # Configuración y logging
│   ├── config.py
│   ├── errors.py
│   └── logging.py
├── schemas/
│   └── report.py           # Modelos Pydantic del informe
└── services/
    ├── layout/             # Segmentación layout-first
    │   ├── models.py       # ZoneType, ZonedPage, PageZone, ZoneToken
    │   ├── segmenters.py   # DigitalPageSegmenter, OcrPageSegmenter
    │   ├── zone_config.py  # Política de detección por zona
    │   └── _digital_analysis.py
    ├── ocr/                # Motores OCR
    │   ├── base.py         # OcrEngine (ABC)
    │   ├── factory.py      # get_ocr_engine(), create_ocr_engine()
    │   ├── easyocr_engine.py
    │   └── mineru_engine.py
    ├── pdf/                # Pipeline PDF
    │   ├── pipeline.py     # PdfAnonymizationPipeline
    │   ├── redactor.py     # Redacción digital
    │   ├── scanned_redactor.py
    │   ├── hybrid_redactor.py
    │   ├── inspector.py
    │   └── extractor.py
    ├── pii/                # Detección de PII
    │   ├── analyzer.py     # PiiAnalyzer (Presidio + regex + NER)
    │   ├── recognizers_es.py
    │   └── validators_es.py
    ├── verification/       # Verificación post-redacción
    │   ├── models.py
    │   ├── layers.py       # 4 capas de verificación
    │   ├── scorer.py
    │   └── verifier.py
    ├── image/
    │   └── pipeline.py     # Pipeline para imágenes sueltas
    └── vision/
        └── code_detector.py  # Detección de QR
```

---

## Instalación

### Requisitos previos

- Python 3.11 o superior
- (Opcional) CUDA para aceleración GPU en OCR

### Instalación básica (EasyOCR como motor OCR)

```bash
git clone <repositorio>
cd document-anonymizer

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

pip install -e .
python -m spacy download es_core_news_md
```

### Instalación con MinerU 2.5 (recomendado)

MinerU ofrece mayor precisión en documentos administrativos y análisis de layout nativo.

```bash
pip install -e ".[mineru]"
# o con el nombre de paquete antiguo:
pip install -e ".[magic-pdf]"

# Descargar modelos de MinerU (requerido la primera vez):
mineru-models-download -m pipeline
# o: magic-pdf-models-download
```

### Variables de entorno

Copia `.env.example` a `.env` y ajusta según tu entorno:

```bash
cp .env.example .env
```

---

## Uso rápido

### Arrancar el servidor

```bash
uvicorn app.main:app --reload
# API disponible en http://localhost:8000
```

### Anonimizar un PDF

```bash
# Devuelve el PDF redactado
curl -X POST http://localhost:8000/anonymize \
  -F "file=@documento.pdf" \
  --output documento_anonimizado.pdf

# Devuelve un ZIP con el PDF + informe JSON
curl -X POST "http://localhost:8000/anonymize?output=zip&mode=strict" \
  -F "file=@documento.pdf" \
  --output resultado.zip
```

### Con Python (httpx)

```python
import httpx

with open("documento.pdf", "rb") as f:
    response = httpx.post(
        "http://localhost:8000/anonymize",
        params={"output": "zip", "mode": "strict"},
        files={"file": ("documento.pdf", f, "application/pdf")},
        timeout=120,
    )

# Cabeceras de respuesta
print(response.headers["x-anonymization-status"])   # "completed"
print(response.headers["x-entities-detected"])       # "15"
print(response.headers["x-entities-redacted"])       # "15"
print(response.headers["x-residual-entities"])       # "0"
```

---

## Modos de redacción

El parámetro `mode` controla la agresividad de la detección:

### `strict` (por defecto)

- Detecta todos los patrones, incluyendo datos ficticios y formatos no válidos.
- Sin validación de checksums (p. ej., acepta DNIs con letra errónea).
- Activa patrones sin dos puntos (`"DNI 12345678Z"`).
- Activa detección de filas de tabla por rol (`"Arquitecto Juan García"`).
- Capas de verificación: `textual`, `metadata`, `binary`.

### `balanced`

- Valida checksums: DNI, NIE, CIF, IBAN deben ser matemáticamente correctos.
- Menor tasa de falsos positivos; puede omitir datos de prueba intencionalmente incorrectos.
- Capas de verificación: `textual`, `metadata`.

---

## Sistema de zonas layout-first

Antes de ejecutar ningún detector, cada página se segmenta en zonas tipadas. Cada zona tiene una política de detección diferente.

### Tipos de zona

| Zona | Descripción | Política |
|------|-------------|----------|
| `HEADER` | Cabecera: nº expediente, organismo, fecha | Tipos reducidos: PERSON, DNI, NIE, CASE_NUMBER, DATE_OF_BIRTH |
| `FOOTER` | Pie: nº página, referencia, sello | Solo CASE_NUMBER, URL |
| `FORM` | Área etiqueta-valor | Todos los tipos + rects pre-calculados estructuralmente |
| `TABLE` | Tabla estructurada | Todos los tipos |
| `BODY` | Texto libre | Todos los tipos |
| `SIGNATURE` | Zona de firma manuscrita | **Redacción total automática** |
| `QR_CODE` | Código QR o de barras | **Redacción total automática** |
| `MARGINALIA` | Anotaciones marginales | Tipos básicos: PERSON, DNI, NIE, EMAIL, PHONE |

### Segmentadores

- **`DigitalPageSegmenter`**: para PDFs con texto seleccionable. Particiona por bandas Y (cabecera < 12%, pie > 88%) y detecta formularios/tablas estructuralmente. Produce `ZonedPage` con `layout_redact_rects` pre-calculados.

- **`OcrPageSegmenter`**: para páginas escaneadas. Dos caminos:
  - Si el motor OCR es MinerU (`provides_layout=True`): usa directamente los tipos de región de MinerU (text/title/table/header/footer → zonas).
  - Si el motor es EasyOCR: detección heurística con `OcrFormLayoutAnalyzer` y `OcrTableLayoutAnalyzer`.

---

## Motores OCR

### MinerU 2.5 (motor principal)

MinerU es un framework de comprensión de documentos desarrollado por Shanghai AI Lab. A diferencia de los motores OCR tradicionales, realiza **análisis de layout antes de extraer texto**, clasificando regiones en tipos semánticos (texto, título, tabla, cabecera, pie, figura...).

Ventajas sobre EasyOCR:
- Mayor precisión en documentos administrativos estructurados.
- Análisis de layout nativo (feeds directo al sistema de zonas).
- Procesa el PDF completo en una sola pasada, sin rasterización previa.
- Reconocimiento de estructura de tablas.

```bash
# Configurar MinerU como motor principal:
OCR_ENGINE=mineru
OCR_MINERU_BACKEND=pipeline_standard  # o pipeline_fast / vlm_openai
```

### EasyOCR (fallback)

Motor OCR token a token. Se activa automáticamente si MinerU no está instalado o falla.

```bash
OCR_ENGINE=easyocr
```

### Modo automático (por defecto)

```bash
OCR_ENGINE=auto  # intenta MinerU, cae a easyocr si no disponible
```

### Añadir un motor nuevo

Basta con crear una clase que implemente `OcrEngine` (ABC en `ocr/base.py`) y registrarla en `ocr/factory.py`:

```python
class MiMotorOCR(OcrEngine):
    @property
    def engine_name(self) -> str:
        return "mi_motor"

    def analyze_image(self, image_path: Path, page_index: int = 0) -> OcrPage:
        ...
```

---

## Verificación post-redacción (4 capas)

Tras cada redacción, el sistema ejecuta hasta 4 capas de verificación independientes y calcula un **score de riesgo por página** en [0.0, 1.0].

### Capas

| Capa | Qué verifica | Peso | Activación por defecto |
|------|-------------|------|----------------------|
| `textual` | Extracción de texto PyMuPDF → PiiAnalyzer | 1.0 | Siempre |
| `metadata` | Info dict, XMP, AcroForm, anotaciones, adjuntos | 0.7 | Siempre |
| `binary` | Bytes crudos del PDF con regex de alta confianza | 0.5 | Siempre (strict) |
| `ocr` | Renderizar página → OCR → PiiAnalyzer | 0.9 | Opt-in (`VERIFY_OCR_LAYER_ENABLED=true`) |

### Fórmula del score de riesgo

```
score_capa = min(1.0, n_entidades / 4) × confianza_media × peso_capa
score_página = media_ponderada(scores_capas_ejecutadas)
score_documento = max(scores_páginas, scores_capas_documento)
```

### Umbrales configurables

| Variable | Defecto | Efecto |
|----------|---------|--------|
| `VERIFY_RISK_THRESHOLD_REVIEW` | `0.30` | `manual_review_required = true` |
| `VERIFY_RISK_THRESHOLD_REJECT` | `0.70` | Advertencia de redacción incompleta |

### Niveles de riesgo

| Rango | Nivel | Significado |
|-------|-------|-------------|
| 0.0 | `none` | No se detectó PII residual |
| 0.0–0.20 | `low` | Posibles falsos positivos |
| 0.20–0.50 | `medium` | Revisar manualmente |
| > 0.50 | `high` | PII probablemente presente |

---

## Tipos de PII detectados

| Tipo | Descripción | Validación en modo balanced |
|------|-------------|---------------------------|
| `ES_DNI` | DNI español | Checksum mod-23 |
| `ES_NIE` | NIE extranjero | Checksum mod-23 |
| `ES_CIF` | CIF empresarial | Checksum por tipo organización |
| `EMAIL_ADDRESS` | Dirección de correo | Formato RFC |
| `PHONE_NUMBER` | Teléfono español (+34) | — |
| `IBAN_CODE` | IBAN español (ES...) | Checksum mod-97 |
| `PERSON` | Nombre de persona | NER (spaCy) |
| `LOCATION` | Lugar geográfico | NER (spaCy) |
| `ORGANIZATION` | Organización | NER (spaCy) |
| `ADDRESS` | Dirección postal | — |
| `PASSPORT` | Número de pasaporte | — |
| `CREDIT_CARD` | Tarjeta bancaria | — |
| `USER_ID` | Identificador de usuario | — |
| `IP_ADDRESS` | Dirección IP | — |
| `MEDICAL_ID` | ID sanitario | — |
| `POLICY_NUMBER` | Número de póliza | — |
| `LICENSE_PLATE` | Matrícula de vehículo | — |
| `CADASTRAL_REFERENCE` | Referencia catastral | Formato catastral |
| `CASE_NUMBER` | Número de expediente | — |
| `PROFESSIONAL_LICENSE` | Número de colegiado | — |
| `POSTAL_CODE` | Código postal español | — |
| `URL` | URL / enlace web | — |
| `DATE_OF_BIRTH` | Fecha de nacimiento | — |

La detección funciona mediante tres mecanismos combinados:
1. **Patrones de etiqueta**: `"DNI: 12345678Z"` → captura el valor a la derecha.
2. **Regex directos**: detecta el patrón en texto libre.
3. **NER**: spaCy `es_core_news_md` para PERSON, LOCATION, ORGANIZATION.

### Soporte valenciano y catalán

Los campos de formulario en valenciano y catalán se detectan igual que en español:

| Valenciano/Catalán | Castellano equivalente |
|-------------------|----------------------|
| `Nom:` | `Nombre:` |
| `Cognoms:` | `Apellidos:` |
| `Correu electronic:` | `Correo electrónico:` |
| `Adreça:` | `Dirección:` |
| `Compte bancari:` | `Cuenta bancaria:` |
| `Expedient:` | `Expediente:` |
| `Data de naixement:` | `Fecha de nacimiento:` |
| `Passaport:` | `Pasaporte:` |
| `Telefon mobil:` | `Teléfono móvil:` |
| `Domicili:` | `Domicilio:` |
| `Codi postal:` | `Código postal:` |

---

## Endpoints de la API

### `GET /health`

Comprueba que el servicio está operativo.

**Respuesta:**
```json
{ "status": "ok" }
```

---

### `POST /anonymize`

Anonimiza un documento PDF o imagen.

**Parámetros de query:**

| Parámetro | Tipo | Defecto | Descripción |
|-----------|------|---------|-------------|
| `output` | `pdf` \| `zip` | `pdf` | Formato de salida |
| `mode` | `strict` \| `balanced` | `strict` | Modo de redacción |

**Body:** `multipart/form-data`

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `file` | `UploadFile` | PDF, PNG, JPG, JPEG o TIFF. Máximo 25 MB y 50 páginas. |

**Cabeceras de respuesta:**

| Cabecera | Descripción |
|----------|-------------|
| `X-Job-Id` | UUID del trabajo |
| `X-Anonymization-Status` | `completed` \| `completed_with_warnings` |
| `X-Entities-Detected` | Nº de entidades detectadas |
| `X-Entities-Redacted` | Nº de entidades redactadas |
| `X-Residual-Entities` | Entidades residuales tras verificación |
| `X-Manual-Review-Required` | `true` si requiere revisión humana |
| `X-Redaction-Mode` | Modo aplicado |
| `X-Output-Format` | `pdf` \| `zip` |

**Respuesta con `output=pdf`:**
- `Content-Type: application/pdf`
- Body: PDF anonimizado

**Respuesta con `output=zip`:**
- `Content-Type: application/zip`
- Contenido del ZIP:
  - `<nombre>_anonymized.pdf` — PDF redactado
  - `report.json` — Informe de auditoría completo

**Informe JSON (`report.json`):**

```json
{
  "job_id": "abc123...",
  "status": "completed",
  "document_type": "pdf_digital",
  "pages": 3,
  "entities_detected": 15,
  "entities_redacted": 15,
  "residual_entities": 0,
  "entities_by_type": {
    "ES_DNI": 2, "PERSON": 5, "EMAIL_ADDRESS": 3,
    "PHONE_NUMBER": 2, "IBAN_CODE": 1, "LAYOUT_VALUE": 2
  },
  "manual_review_required": false,
  "warnings": [],
  "redaction_mode": "strict",
  "verification": {
    "overall_risk_score": 0.0,
    "risk_level": "none",
    "layers_run": ["binary", "metadata", "textual"],
    "critical_pages": [],
    "entities_by_layer": {},
    "total_residual_entities": 0,
    "passed": true
  },
  "ocr": {
    "enabled": false,
    "engine": null
  },
  "processing_ms": 1240
}
```

**Códigos de error:**

| Código | Significado |
|--------|-------------|
| 400 | Fichero inválido o firma PDF incorrecta |
| 413 | Fichero demasiado grande (> 25 MB) |
| 415 | Tipo de contenido no soportado |
| 422 | PDF cifrado, vacío o con demasiadas páginas |
| 500 | Error interno de procesamiento |

---

## Configuración

Todas las variables se configuran en el fichero `.env` (o como variables de entorno).

### Variables principales

| Variable | Defecto | Descripción |
|----------|---------|-------------|
| `MAX_UPLOAD_MB` | `25` | Tamaño máximo del fichero subido |
| `MAX_PDF_PAGES` | `50` | Número máximo de páginas |
| `PRESIDIO_SCORE_THRESHOLD` | `0.55` | Umbral mínimo de confianza Presidio |
| `DEFAULT_REDACTION_MODE` | `strict` | Modo por defecto si no se especifica |
| `PDF_PROCESSING_MODE` | `hybrid_secure` | `auto` \| `raster_ocr` \| `hybrid_secure` |

### Variables OCR

| Variable | Defecto | Descripción |
|----------|---------|-------------|
| `OCR_ENGINE` | `auto` | `auto` \| `mineru` \| `easyocr` |
| `OCR_USE_GPU` | `true` | Activar GPU en OCR |
| `OCR_FALLBACK_ENGINE` | `easyocr` | Motor cuando el principal no está disponible |
| `OCR_MINERU_BACKEND` | `pipeline_standard` | Backend de MinerU |
| `OCR_PDF_RENDER_DPI` | `200` | DPI para renderizar páginas escaneadas |
| `OCR_SECOND_PASS_VERIFICATION` | `true` | Segunda pasada OCR sobre la imagen redactada |

### Variables de verificación

| Variable | Defecto | Descripción |
|----------|---------|-------------|
| `VERIFY_RISK_THRESHOLD_REVIEW` | `0.30` | Score ≥ umbral → revisión manual |
| `VERIFY_RISK_THRESHOLD_REJECT` | `0.70` | Score ≥ umbral → advertencia de fallo |
| `VERIFY_LAYERS_STRICT` | `textual,metadata,binary` | Capas activas en modo strict |
| `VERIFY_LAYERS_BALANCED` | `textual,metadata` | Capas activas en modo balanced |
| `VERIFY_OCR_LAYER_ENABLED` | `false` | Activar capa OCR de verificación |

### Variables de almacenamiento

| Variable | Defecto | Descripción |
|----------|---------|-------------|
| `TEMP_ROOT` | `/tmp/anonymizer` | Directorio de ficheros temporales |
| `DELETE_TEMP_FILES` | `true` | Borrar ficheros temporales tras procesar |
| `LOG_PII` | `false` | Si `true`, registra valores PII en logs (no recomendado en producción) |

---

## Docker

### Arranque con Docker Compose

```bash
docker compose up --build
# API disponible en http://localhost:8000
```

### Variables de entorno en Docker

```yaml
# docker-compose.yml (fragmento)
environment:
  OCR_ENGINE: auto
  VERIFY_LAYERS_STRICT: textual,metadata,binary
  MAX_UPLOAD_MB: 25
```

### Imagen de producción

```bash
docker build -t document-anonymizer:latest .
docker run -p 8000:8000 \
  -e OCR_ENGINE=mineru \
  -e VERIFY_OCR_LAYER_ENABLED=false \
  document-anonymizer:latest
```

> **Nota sobre MinerU en Docker**: la imagen base no incluye los pesos del modelo de MinerU. Para un despliegue con MinerU, monta un volumen con los modelos pre-descargados o añade el paso de descarga al `Dockerfile`.

---

## Tests

```bash
pip install -e ".[test]"
pytest tests/ -v
```

La suite incluye tests de:
- Validación de API (formato, tamaño, tipo de fichero)
- Redacción de PDFs digitales y escaneados
- Detección de PII en español (DNI, NIE, CIF, IBAN, email, teléfono)
- Layout de tablas y formularios
- Seguridad (metadatos, PII en informes)
- Patrones ficticios (modo strict vs balanced)
