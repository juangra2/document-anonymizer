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

**Motores de OCR** (arquitectura de doble motor en páginas escaneadas):

- **MinerU** lee: devuelve texto limpio por bloques en orden de lectura correcto (respeta columnas, reconstruye tablas como filas etiqueta:valor). Toda la detección PII corre sobre su texto.
- **EasyOCR** sitúa: aporta cajas por palabra/línea con coordenadas de píxel. Los spans detectados se mapean a esas cajas por contenido — con tolerancia a confusiones de OCR (S↔5, O↔0, I/L↔1, B↔8) — para pintar rectángulos ajustados. También ejecuta la segunda pasada de verificación sobre la imagen ya redactada.

Si MinerU no está disponible, EasyOCR asume ambos papeles como *fallback*.

**Planos de obra (A1/A0):** las páginas de plano se detectan **antes de rasterizar** (formato, dominancia gráfica, ausencia de párrafos — solo metadatos, O(ms)) y siguen un camino propio: se localiza el **cajetín** (por capa de texto en planos CAD; por visión OpenCV + validación OCR de palabras clave en escaneados) y se analiza y redacta **únicamente ese recorte**. El cuerpo del dibujo nunca se procesa; sin cajetín detectable, la página se conserva intacta y se marca para revisión manual.

**Modo de redacción:** único (`strict`). Los validadores de checksum (mod-23 del DNI, mod-97 del IBAN…) se usan como evidencia positiva, nunca como veto: un identificador con un dígito mal leído por el OCR sigue siendo PII.

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
| **EasyOCR** | Geometría por palabra, verificación y respaldo |
| **OpenCV** + **pyzbar** | Detección de códigos QR y de barras; localización del cajetín en planos |

Datos públicos del **INE** (apellidos, nombres y municipios) se usan como *gazetteers*: validan direcciones (CP↔provincia↔municipio), avalan nombres de persona y alimentan un **detector propio de nombres en texto corrido** que recupera las menciones que el NER pierde en prosa administrativa.

---

## Tipos de PII detectados

18 tipos activos sobre el corpus de evaluación:

| Tipo | Descripción |
|---|---|
| `PERSON` | Nombres y apellidos (NER + detector gazetteer INE en prosa, bloques de firma «Fdo. / Signat per», celdas de formulario y nombres de fichero adjunto) |
| `ES_DNI` | DNI/NIF español, con y sin separadores de miles (12345678A, 47.215.601-M) |
| `ES_NIE` | NIE de extranjeros (X/Y/Z + 7 dígitos + letra) |
| `ES_CIF` | CIF de entidades jurídicas |
| `ES_NSS` | Número de afiliación a la Seguridad Social |
| `EMAIL_ADDRESS` | Direcciones de correo electrónico |
| `PHONE_NUMBER` | Teléfonos fijos y móviles (formato español e internacional) |
| `IBAN_CODE` | Cuentas bancarias IBAN y CCC pre-SEPA (4-4-2-10); tolerante a errores de OCR |
| `ADDRESS` | Direcciones postales (extractor compositivo con validación CP↔provincia↔municipio del INE) |
| `POSTAL_CODE` | Códigos postales (01000–52999) |
| `PASSPORT` | Números de pasaporte |
| `IP_ADDRESS` | Direcciones IPv4 |
| `LICENSE_PLATE` | Matrículas de vehículos |
| `CASE_NUMBER` | Números de expediente / referencia administrativa |
| `URL` | Direcciones web |
| `DATE_OF_BIRTH` | Fecha de nacimiento |
| `DATE_TIME` | Fechas y horas |
| `QR_CODE` | Códigos QR y de barras lineales (Code 128/39, EAN, PDF417…) — solo se redactan si decodifican a contenido real |

Las etiquetas de campo se reconocen en castellano y valenciano/catalán, incluidas las formas bilingües de formularios GVA («NOM / NOMBRE», «COGNOMS / APELLIDOS»…) con el valor en la línea siguiente, y los campos financieros de transferencias (ordenante, beneficiario, entidad bancaria, cuenta del beneficiario).

---

## Métricas

Evaluación sobre un **corpus sintético de 400 documentos administrativos españoles** (castellano y valenciano).

| | Precision | Recall | F1 |
|---|---|---|---|
| **Baseline inicial** | 0.719 | 0.753 | 0.736 |
| **Versión actual** | **0.911** | **0.939** | **0.925** |

### Métricas actuales por tipo de entidad

| Tipo | Precision | Recall | F1 |
|---|---|---|---|
| `CASE_NUMBER` | 1.000 | 0.998 | 0.999 |
| `IBAN_CODE` | 0.990 | 0.997 | 0.993 |
| `ES_DNI` | 0.991 | 0.987 | 0.989 |
| `EMAIL_ADDRESS` | 0.990 | 0.987 | 0.988 |
| `ES_NSS` | 0.987 | 0.987 | 0.987 |
| `ES_NIE` | 0.979 | 0.963 | 0.971 |
| `ES_CIF` | 1.000 | 0.941 | 0.970 |
| `URL` | 1.000 | 0.918 | 0.957 |
| `DATE_TIME` | 0.920 | 0.970 | 0.944 |
| `PASSPORT` | 0.923 | 0.947 | 0.935 |
| `QR_CODE` | 1.000 | 0.877 | 0.934 |
| `IP_ADDRESS` | 1.000 | 0.865 | 0.928 |
| `POSTAL_CODE` | 0.941 | 0.874 | 0.906 |
| `LICENSE_PLATE` | 0.918 | 0.889 | 0.903 |
| `ADDRESS` | 0.881 | 0.845 | 0.863 |
| `DATE_OF_BIRTH` | 0.972 | 0.775 | 0.862 |
| `PERSON` | 0.779 | 0.962 | 0.861 |
| `PHONE_NUMBER` | 0.856 | 0.832 | 0.844 |

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
| `mode` | query | `strict` (único valor admitido) |
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

- **`PERSON` (P=0.78, R=0.96)** prioriza deliberadamente el recall: el detector por gazetteer INE recupera los nombres que el NER pierde en prosa a costa de algunos falsos positivos, en su mayoría **institucionales** (organismos, frases administrativas en valenciano/catalán) heredados del NER.
- Sistema **optimizado para documentos administrativos españoles** en castellano y valenciano/catalán; otros dominios o idiomas no están garantizados.
- Las **métricas se han obtenido sobre un corpus sintético** de 400 documentos; el rendimiento sobre documentos reales puede variar.
