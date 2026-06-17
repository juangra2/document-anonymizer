# document-anonymizer

API REST de **anonimización irreversible de documentos PDF** para la administración pública española. Detecta información personal (PII), la **redacta físicamente** sobre el documento (no es un simple recuadro superpuesto) y elimina todos los metadatos del archivo resultante.

---

## Qué es

Servicio HTTP que recibe un PDF (o imagen escaneada) y devuelve una versión anonimizada en la que los datos personales han sido eliminados de forma permanente del contenido y de los metadatos. Está diseñado para documentos administrativos españoles —resoluciones, notificaciones, certificados, contratos, formularios— en castellano y valenciano/catalán.

---

## Cómo funciona

El sistema sigue un pipeline **layout-first**: cada página se segmenta en zonas tipadas *antes* de ejecutar ningún detector de PII, de modo que cada zona aplica su propia política de detección y redacción.

```
PDF / imagen
     │
     ▼
1. Inspección        ¿documento digital, escaneado o híbrido?
     │
     ▼
2. Segmentación      HEADER · FOOTER · FORM · TABLE · BODY · SIGNATURE · QR_CODE
   por zonas
     │
     ▼
3. Detección PII     • FORM / TABLE   → redacción estructural (campos y celdas)
   y redacción       • SIGNATURE / QR → redacción total de la zona
   por zona          • BODY/HEADER/…  → análisis PII con tipos restringidos por zona
     │
     ▼
4. Verificación      4 capas: textual · metadata · binary · OCR
   post-redacción    (confirma que no queda PII residual)
     │
     ▼
PDF anonimizado + metadatos eliminados
```

**Modos de procesamiento** (seleccionados automáticamente según el documento):

- **Digital** — texto extraíble con PyMuPDF; redacción a nivel de palabra mediante mapeo de offsets a rectángulos.
- **Escaneado (OCR)** — páginas rasterizadas; el texto y su geometría se obtienen por OCR.
- **Híbrido** — combina texto digital y OCR cuando el PDF mezcla ambos.

**Motores de OCR:** **MinerU** es el motor principal (entiende la estructura del documento: bloques, tablas, cabeceras), con **EasyOCR** como *fallback* automático cuando MinerU no está disponible.

**Modos de redacción:** `strict` (todas las heurísticas, por defecto) y `balanced` (solo detecciones validadas, menos agresivo).

---

## Stack tecnológico

| Componente | Uso |
|---|---|
| **Python 3.12** | Lenguaje base |
| **FastAPI** + Uvicorn | API REST asíncrona |
| **Microsoft Presidio** | Orquestación de reconocedores de PII |
| **spaCy** (`es_core_news_md`) | NER de personas y entidades |
| **PyMuPDF** | Extracción de texto/geometría y redacción física del PDF |
| **MinerU** | Motor OCR principal con comprensión de layout |
| **EasyOCR** | Motor OCR de respaldo |

Datos públicos del **INE** (apellidos, nombres y municipios) se usan como *gazetteers* para reforzar la detección de direcciones y personas.

---

## Tipos de PII detectados

18 tipos activos sobre el corpus de evaluación:

| Tipo | Descripción |
|---|---|
| `PERSON` | Nombres y apellidos de personas físicas |
| `ES_DNI` | DNI español (8 dígitos + letra de control) |
| `ES_NIE` | NIE de extranjeros (X/Y/Z + 7 dígitos + letra) |
| `ES_CIF` | CIF de entidades jurídicas |
| `ES_NSS` | Número de afiliación a la Seguridad Social |
| `EMAIL_ADDRESS` | Direcciones de correo electrónico |
| `PHONE_NUMBER` | Teléfonos fijos y móviles (formato español e internacional) |
| `IBAN_CODE` | Cuentas bancarias IBAN (validación mod-97) |
| `ADDRESS` | Direcciones postales (extractor compositivo con validación CP↔provincia↔municipio del INE) |
| `POSTAL_CODE` | Códigos postales (01000–52999) |
| `PASSPORT` | Números de pasaporte |
| `IP_ADDRESS` | Direcciones IPv4 |
| `LICENSE_PLATE` | Matrículas de vehículos |
| `CASE_NUMBER` | Números de expediente / referencia administrativa |
| `URL` | Direcciones web |
| `DATE_OF_BIRTH` | Fecha de nacimiento |
| `DATE_TIME` | Fechas y horas |
| `QR_CODE` | Códigos QR / barras (redacción total + análisis del contenido) |

---

## Métricas

Evaluación sobre un **corpus sintético de 400 documentos administrativos españoles** (castellano y valenciano).

| | Precision | Recall | F1 |
|---|---|---|---|
| **Baseline inicial** | 0.719 | 0.753 | 0.736 |
| **Versión actual** | **0.924** | **0.909** | **0.916** |

### Métricas actuales por tipo de entidad

| Tipo | Precision | Recall | F1 |
|---|---|---|---|
| `CASE_NUMBER` | 1.000 | 1.000 | 1.000 |
| `ES_DNI` | 0.991 | 0.987 | 0.989 |
| `ES_NSS` | 0.987 | 0.987 | 0.987 |
| `ES_NIE` | 0.980 | 0.980 | 0.980 |
| `ES_CIF` | 1.000 | 0.941 | 0.970 |
| `EMAIL_ADDRESS` | 0.966 | 0.964 | 0.965 |
| `IBAN_CODE` | 0.996 | 0.929 | 0.962 |
| `URL` | 1.000 | 0.918 | 0.957 |
| `DATE_TIME` | 0.920 | 0.970 | 0.944 |
| `QR_CODE` | 1.000 | 0.877 | 0.934 |
| `PASSPORT` | 0.922 | 0.939 | 0.930 |
| `IP_ADDRESS` | 1.000 | 0.865 | 0.928 |
| `POSTAL_CODE` | 0.942 | 0.872 | 0.906 |
| `LICENSE_PLATE` | 0.917 | 0.873 | 0.894 |
| `ADDRESS` | 0.882 | 0.844 | 0.863 |
| `DATE_OF_BIRTH` | 0.972 | 0.775 | 0.862 |
| `PHONE_NUMBER` | 0.855 | 0.829 | 0.842 |
| `PERSON` | 0.803 | 0.820 | 0.811 |

> El baseline inicial solo se registró a nivel global (F1=0.736); no existe desglose por tipo de aquella versión, por lo que la tabla por entidad refleja únicamente la versión actual.

---

## Instalación

Requiere **Python 3.12**.

```bash
# 1. Entorno virtual
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2. Dependencias
pip install -e .

# 3. Modelo de spaCy en español
python -m spacy download es_core_news_md
```

MinerU es opcional (se usa EasyOCR como respaldo). Para habilitarlo:

```bash
pip install -e ".[mineru]"
```

## Arranque

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

La API queda disponible en `http://localhost:8000`. Documentación interactiva en `http://localhost:8000/docs`.

---

## Uso

**Endpoint:** `POST /anonymize`

| Parámetro | Tipo | Descripción |
|---|---|---|
| `file` | multipart | Documento a anonimizar (PDF, PNG, JPG/JPEG, TIFF) |
| `mode` | query | `strict` (por defecto) o `balanced` |
| `output` | query | `pdf` (por defecto) o `zip` (incluye `report.json`) |
| `include_entities` | query | `true` para incluir la lista de entidades detectadas en el informe (auditoría) |

**curl**

```bash
curl -X POST "http://localhost:8000/anonymize?mode=strict&output=pdf" \
  -F "file=@documento.pdf" \
  -o documento_anonimizado.pdf
```

**Python**

```python
import requests

with open("documento.pdf", "rb") as f:
    r = requests.post(
        "http://localhost:8000/anonymize",
        params={"mode": "strict", "output": "zip", "include_entities": True},
        files={"file": ("documento.pdf", f, "application/pdf")},
    )

with open("resultado.zip", "wb") as out:
    out.write(r.content)
```

---

## Limitaciones conocidas

- **`PERSON` (F1=0.81)** es el tipo con menor precisión: la mayoría de sus falsos positivos provienen de nombres **institucionales** (organismos, empresas, cargos) que el NER confunde con personas físicas.
- Sistema **optimizado para documentos administrativos españoles** en castellano y valenciano/catalán; otros dominios o idiomas no están garantizados.
- Las **métricas se han obtenido sobre un corpus sintético** de 400 documentos; el rendimiento sobre documentos reales puede variar.
