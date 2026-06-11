# document-anonymizer

API REST de **anonimización irreversible de documentos PDF** para la administración pública española. Detecta y redacta información personal (PII) de forma física y elimina todos los metadatos del archivo resultante.

---

## Arquitectura

El sistema sigue un pipeline **layout-first**: cada página se segmenta en zonas tipadas antes de ejecutar ningún detector de PII.

```
PDF / imagen
     │
     ▼
Inspector  →  ¿Digital / Escaneado / Híbrido?
     │
     ▼
Segmentador de zonas
  HEADER · FOOTER · FORM · TABLE · BODY · SIGNATURE · QR_CODE
     │
     ▼
Redactor por zona
  FORM / TABLE  →  redacción estructural (campos y celdas completas)
  SIGNATURE / QR  →  redacción total automática de la zona
  BODY / HEADER / FOOTER  →  PiiAnalyzer con tipos restringidos por zona
     │
     ▼
Verificación post-redacción (4 capas: textual · metadata · binary · OCR)
     │
     ▼
PDF anonimizado + informe JSON
```

### Modos de procesamiento

| Modo | Descripción |
|------|-------------|
| **Digital** | PDF con texto seleccionable — PyMuPDF extrae el texto directamente |
| **OCR** | Documento escaneado — MinerU analiza el layout y extrae el texto |
| **Híbrido** | Páginas mixtas — cada página se procesa por el modo que le corresponde |

### Motores OCR

- **MinerU 2.5** (motor principal): analiza el layout completo antes de extraer texto y clasifica cada región en un tipo semántico (texto, título, tabla, cabecera, pie) que alimenta directamente el sistema de zonas.
- **EasyOCR** (fallback): se activa automáticamente si MinerU no está disponible o falla.

---

## Stack tecnológico

| Capa | Tecnología |
|------|-----------|
| Framework web | FastAPI 0.115 + Uvicorn |
| Detección de PII | Microsoft Presidio 2.2 + spaCy 3.7 (`es_core_news_md`) |
| Manipulación de PDF | PyMuPDF (fitz) 1.24 |
| OCR principal | MinerU 2.5 |
| OCR fallback | EasyOCR |
| Visión por computador | OpenCV (firmas, QR) |
| Python | 3.11+ |

---

## Tipos de PII detectados

| Tipo | Descripción |
|------|-------------|
| `ES_DNI` | DNI español (`12345678Z`) |
| `ES_NIE` | NIE de extranjero residente (`X1234567Z`) |
| `ES_CIF` | CIF de empresa u organismo público |
| `ES_NSS` | Número de afiliación a la Seguridad Social |
| `PERSON` | Nombre y apellidos (NER + patrones etiquetados en ES/CA/VAL) |
| `EMAIL_ADDRESS` | Dirección de correo electrónico |
| `PHONE_NUMBER` | Número de teléfono español (fijo y móvil) |
| `IBAN_CODE` | Código IBAN español (`ES...`) |
| `ADDRESS` | Dirección postal completa con código postal |
| `POSTAL_CODE` | Código postal de 5 dígitos |
| `DATE_OF_BIRTH` | Fecha de nacimiento en cualquier formato habitual |
| `PASSPORT` | Número de pasaporte |
| `CREDIT_CARD` | Número de tarjeta bancaria (solo con etiqueta explícita) |
| `IP_ADDRESS` | Dirección IP versión 4 |
| `LICENSE_PLATE` | Matrícula de vehículo española |
| `CADASTRAL_REFERENCE` | Referencia catastral de inmueble |
| `CASE_NUMBER` | Número de expediente administrativo |
| `PROFESSIONAL_LICENSE` | Número de colegiado profesional |
| `URL` | Enlace web |

La detección combina tres mecanismos: **patrones etiquetados** (`DNI: 12345678Z`), **expresiones regulares** en texto libre y **NER** con spaCy para nombres propios. Las etiquetas en valenciano y catalán (`Nom:`, `Cognoms:`, `Adreça:`, `Data de naixement:`…) se detectan igual que en castellano.

---

## Métricas

Evaluadas sobre un corpus sintético de **400 documentos** administrativos en modo `strict`.

**Globales:** Precisión = 0.913 · Recall = 0.851 · **F1 = 0.881**

| Tipo | F1 | Precisión | Recall |
|------|----|-----------|--------|
| ES_CIF | 1.000 | 1.000 | 1.000 |
| ES_NSS | 1.000 | 1.000 | 1.000 |
| CASE_NUMBER | 1.000 | 1.000 | 1.000 |
| ES_DNI | 0.986 | 0.987 | 0.984 |
| ES_NIE | 0.973 | 0.979 | 0.968 |
| URL | 0.957 | 1.000 | 0.918 |
| EMAIL_ADDRESS | 0.950 | 0.939 | 0.961 |
| PASSPORT | 0.930 | 0.922 | 0.939 |
| IP_ADDRESS | 0.928 | 1.000 | 0.865 |
| IBAN_CODE | 0.925 | 0.960 | 0.892 |
| LICENSE_PLATE | 0.923 | 0.895 | 0.952 |
| PERSON | 0.833 | 0.856 | 0.811 |
| DATE_OF_BIRTH | 0.788 | 1.000 | 0.650 |
| PHONE_NUMBER | 0.735 | 0.652 | 0.841 |
| POSTAL_CODE | 0.680 | 0.926 | 0.537 |
| ADDRESS | 0.636 | 0.813 | 0.522 |

> Las métricas corresponden a un corpus sintético y pueden variar en documentos reales en función del tipo de formulario, la calidad del escaneado y las convenciones de redacción de cada organismo.

---

## Instalación

**Con MinerU (recomendado):**

```bash
git clone <repositorio>
cd document-anonymizer
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[mineru]"
python -m spacy download es_core_news_md
mineru-models-download -m pipeline   # descarga los modelos la primera vez
```

**Solo EasyOCR:**

```bash
pip install -e .
python -m spacy download es_core_news_md
```

Copia `.env.example` a `.env` y ajusta las variables si es necesario.

---

## Arrancar el servidor

```bash
uvicorn app.main:app --reload
# API disponible en http://localhost:8000
```

---

## Uso

### curl

```bash
# Devuelve el PDF anonimizado
curl -X POST http://localhost:8000/anonymize \
  -F "file=@documento.pdf" \
  --output documento_anonimizado.pdf

# Devuelve un ZIP con el PDF + informe JSON de auditoría
curl -X POST "http://localhost:8000/anonymize?output=zip" \
  -F "file=@documento.pdf" \
  --output resultado.zip
```

### Python

```python
import httpx

with open("documento.pdf", "rb") as f:
    r = httpx.post(
        "http://localhost:8000/anonymize",
        params={"output": "zip"},
        files={"file": ("documento.pdf", f, "application/pdf")},
        timeout=120,
    )

print(r.headers["x-anonymization-status"])  # "completed"
print(r.headers["x-entities-detected"])      # "18"
print(r.headers["x-residual-entities"])      # "0"
```

El parámetro `mode` controla la agresividad: `strict` (por defecto) acepta datos ficticios y no valida checksums; `balanced` valida DNI, NIE, CIF e IBAN matemáticamente para reducir falsos positivos.

---

## Limitaciones conocidas

- **ADDRESS y POSTAL_CODE** tienen recall bajo (F1 ≈ 0.64–0.68): los saltos de línea introducidos por la extracción de texto PDF dificultan la detección de direcciones que se parten en dos líneas.
- **PHONE_NUMBER** tiene precisión baja (P ≈ 0.65): algunos números de referencia o identificadores numéricos se detectan erróneamente como teléfonos.
- **DATE_OF_BIRTH** tiene recall bajo (R = 0.65): etiquetas muy abreviadas o formatos de fecha poco convencionales pueden no detectarse.
- El sistema está optimizado para documentos administrativos españoles en **castellano, valenciano y catalán**. Documentos en otros idiomas o con formatos muy heterodoxos tendrán peor rendimiento.
- Los documentos escaneados con resolución inferior a 150 DPI pueden tener detección incompleta por errores de OCR.
