# document-anonymizer

API REST que anonimiza de forma **irreversible** documentos PDF de la administración pública
(Ajuntament de València), en castellano y valenciano. Detecta datos personales, los **borra
físicamente** del documento —el texto tachado desaparece del PDF, no queda debajo de un recuadro—
y elimina los metadatos del fichero resultante.

Recibe expedientes digitales, escaneados o mezclados, desde una página hasta tomos de cientos, y
devuelve el PDF anonimizado junto con un informe de lo que se ha hecho y de lo que conviene revisar
a mano.

---

## Índice

1. [Qué datos anonimiza](#qué-datos-anonimiza)
2. [Cómo funciona](#cómo-funciona)
3. [API](#api)
4. [Configuración](#configuración)
5. [Ejecución en local](#ejecución-en-local)
6. [Evaluación](#evaluación)
7. [Métricas actuales](#métricas-actuales)
8. [Limitaciones conocidas y pendientes](#limitaciones-conocidas-y-pendientes)
9. [Documentos reales de prueba](#documentos-reales-de-prueba)

---

## Qué datos anonimiza

31 tipos de dato: 30 tipos de texto más los códigos QR y de barras. `GET /entity-types` devuelve
este catálogo con nombre legible y descripción para la interfaz.

**Todos son desmarcables y todos están activos por defecto.** Cada petición puede excluir los que
no quiera anonimizar con `disabled_types`; lo que se haya desmarcado queda escrito en
`entity_types_disabled` del informe, que es el rastro de quién decidió no ocultar qué. La API solo
rechaza un tipo que no exista.

| Tipo | Qué captura |
|---|---|
| `PERSON` | Nombres y apellidos de personas físicas, incluidos los de firma («Fdo.: …») |
| `ES_DNI` | DNI/NIF, con o sin puntos (12345678A, 47.215.601-M) |
| `ES_NIE` | NIE de extranjero (X/Y/Z + 7 cifras + letra) |
| `ES_NSS` | Número de afiliación a la Seguridad Social |
| `IBAN_CODE` | Cuentas IBAN y cuentas antiguas de 20 dígitos (CCC) |
| `DATE_OF_BIRTH` | Fecha de nacimiento |
| `ES_CIF` | CIF de empresas y entidades |
| `PASSPORT` | Número de pasaporte |
| `ORGANIZATION` | Razón social de empresa **privada** (las instituciones públicas no se anonimizan) |
| `ADDRESS` | Domicilios: tipo de vía, número, piso y municipio |
| `POSTAL_CODE` | Código postal |
| `PHONE_NUMBER` | Teléfonos fijos y móviles, formato español o internacional |
| `EMAIL_ADDRESS` | Correo electrónico |
| `MONEY` | Importes ligados a una persona o cuenta (nóminas, transferencias, liquidaciones) |
| `BUDGET_AMOUNT` | Precios, mediciones y totales de un presupuesto de obra. Marcado por defecto como los demás: se desmarca cuando el presupuesto debe quedar a la vista |
| `CREDIT_CARD` | Tarjeta de crédito o débito |
| `DATE_TIME` | Cualquier otra fecha, incluidas las de registro y firma |
| `LICENSE_PLATE` | Matrícula de vehículo |
| `CADASTRAL_REFERENCE` | Referencia catastral |
| `CASE_NUMBER` | Referencias de expediente administrativo |
| `REGISTRY_NUMBER` | Número de registro de entrada o salida |
| `CSV` | Código seguro de verificación |
| `CERT_SERIAL` | Número de serie del certificado de firma electrónica |
| `MEDICAL_ID` | Historia clínica o tarjeta sanitaria |
| `POLICY_NUMBER` | Número de póliza de seguro |
| `USER_ID` | Nombres de usuario e identificadores internos |
| `IP_ADDRESS` | Direcciones IPv4 |
| `URL` | Enlaces y direcciones web |
| `CASE_TABLE_ROW` | Filas de tabla que combinan expediente e interesado |
| `ROLE_TABLE_ROW` | Filas de tabla que asocian un cargo con una persona |
| `QR_CODE` | Códigos QR y de barras (ver abajo) |

Reglas que conviene conocer:

- **Número de colegiado** (`PROFESSIONAL_LICENSE`): se detecta pero **nunca** se tacha, por
  requisito del ayuntamiento. No aparece en el catálogo.
- **Instituciones públicas**: su nombre no se anonimiza, y tampoco el teléfono o la dirección que
  aparecen junto a él en el membrete.
- **`QR_CODE`**: si está activo, se tachan todos los códigos. Si se desmarca, un código queda
  visible solo si se ha podido leer y su contenido **no lleva ningún tipo que siga activo**; un
  código ilegible se tacha igualmente. En la práctica, el QR de verificación de la sede lleva
  dentro una URL y un CSV, así que conservarlo exige desmarcar también `URL` y `CSV`.
- Las etiquetas de campo se reconocen en castellano y valenciano, incluidas las formas bilingües de
  los impresos de la GVA («NOM / NOMBRE», «COGNOMS / APELLIDOS»).

---

## Cómo funciona

### Reparto por tipo de documento

`services/pdf/pipeline.py` inspecciona el PDF y lo envía por uno de tres caminos:

| Documento | Camino | Qué hace |
|---|---|---|
| Digital (texto seleccionable) | `redactor.py` | Texto y geometría con PyMuPDF; el tachado borra las palabras del contenido del PDF |
| Escaneado | `scanned_redactor.py` | Rasteriza cada página, OCR, tacha sobre la imagen y reconstruye el PDF con imágenes |
| Mixto con poco texto (< 200 caracteres/página) | `hybrid_redactor.py` | Pasada digital sobre el texto y OCR sobre lo escaneado |
| Mixto con texto abundante | `redactor.py` | Las imágenes son iconos o QR; el camino digital ya cubre el contenido |

Las imágenes sueltas (PNG, JPG, TIFF) siguen su propio pipeline con EasyOCR y solo se admiten en
`POST /anonymize`.

### Segmentación por zonas

Antes de detectar nada, cada página se divide en zonas tipadas: `HEADER`, `FOOTER`, `BODY`, `FORM`,
`TABLE`, `SIGNATURE` y `QR_CODE` (`services/layout/`). Cada zona tiene su política
(`zone_config.py`): qué detectores corren y qué se tacha. Por ejemplo, en `HEADER` no se buscan
teléfonos, porque ahí son los de la institución.

En formularios y tablas se hace **redacción estructural**: se reconoce la etiqueta («DNI:», «Nom i
cognoms:») y se tacha su valor, con el tipo que corresponde a la etiqueta (`pii/label_types.py`).
El valor se corta en el canal entre columnas, en la siguiente etiqueta de la misma fila o en un
salto grande, para no comerse el campo vecino (`layout/value_split.py`).

### Detección

`services/pii/analyzer.py` combina:

- **Reconocedores deterministas** (`recognizers_es.py`) para identificadores con formato. Los
  dígitos de control (DNI módulo 23, IBAN módulo 97) cuentan como evidencia a favor, nunca como
  veto: un DNI con un dígito mal leído por el OCR sigue siendo un DNI.
- **NER de spaCy** (`es_core_news_md`) orquestado por Presidio para nombres de persona y
  organizaciones.
- **Gazetteers del INE** (nombres, apellidos, municipios, provincias): validan direcciones
  (código postal ↔ provincia ↔ municipio), refuerzan nombres dudosos y alimentan un detector de
  nombres en prosa que recupera lo que el NER pierde.
- **Filtros de contexto**: institución pública, importes solo junto a persona o cuenta, razón
  social solo con señales de empresa privada.

Los tipos desactivados en la petición se filtran **antes** de resolver solapes entre detectores,
para que un tipo desactivado no pueda ocultar ni sustituir a otro activo.

### Punto único de salida: `RedactionPlan`

Todo lo que produce un rectángulo a tachar —analizador, redacción estructural, zonas de firma y QR,
heurísticas del cajetín— lo da de alta en un `RedactionPlan` por página
(`services/pdf/redaction_plan.py`). **Nadie pinta ni cuenta por su cuenta.** El plan aplica en un
único sitio el filtro de tipos desactivados y la regla del colegiado, admite solo excepciones con
nombre (página completa, QR con datos de tipos activos) y deduplica. Los rectángulos que se pintan y
las entidades que cuenta el informe salen de la misma lista.

### Tratamientos especiales

- **Página completa** (`pdf/full_redaction.py`): la copia de un DNI y el impreso de
  autoliquidación tributaria (modelos 046, 600, 620…) se tachan enteros. Se decide por señales
  textuales distintas con un mínimo configurable, página a página, porque un expediente real
  concatena documentos distintos. En una copia de DNI escaneada se intenta tachar solo la tarjeta:
  se pinta la región, se vuelve a pasar OCR y, si queda texto legible fuera, se tacha la página
  entera.
- **Planos de obra** (`pdf/plan_pages.py`, `vision/titleblock.py`): las páginas A2 o mayores con
  mucho dibujo y sin párrafos se detectan **antes de rasterizar**, solo con metadatos. Se localiza
  el cajetín y se analiza únicamente ese recorte. Si no se encuentra cajetín, la página se deja
  intacta y se marca para revisión manual.
- **Páginas dominadas por una imagen** (planos o fotos escaneados de más de 25 MP): no se hace OCR
  de texto; solo se tachan los códigos visuales. Se marcan para revisión.
- **Escaneos girados** (`ocr/orientation.py`): si el OCR de la página sale como ruido, se prueban
  los giros de 90°, 270° y 180° a resolución completa y se elige por longitud de token y confianza.
- **Páginas ilegibles**: una página escaneada que el OCR no puede leer en ningún giro, o que agota
  el tiempo de OCR por página, se **tacha entera**. Lo que no se ha podido leer tampoco se ha podido
  revisar.

### Motores de OCR

- **MinerU 2.5-Pro** (VLM, en GPU): lee la página con orden de lectura y estructura. La detección de
  datos corre sobre su texto.
- **EasyOCR**: da la caja de cada palabra para situar los tachados, hace la segunda pasada de
  verificación sobre la imagen ya tachada y sustituye a MinerU cuando no está disponible.

### Verificación posterior

Sobre el PDF de salida de los caminos digital e híbrido se ejecutan varias capas
(`services/verification/`): texto extraíble, metadatos (Info, XMP, formularios, anotaciones,
adjuntos, JavaScript) y bytes crudos; la capa de OCR es opcional. Si queda algo, el informe lo marca
con `manual_review_required`. En el camino escaneado, la segunda pasada de OCR cumple el mismo
papel.

---

## Interfaz web

La página está en `app/web/` y la sirve la propia aplicación en `/`: HTML, CSS y módulos de
JavaScript sin paso de compilación, sin Node y sin pedir nada a internet (la red del ayuntamiento
puede no tener salida). Se monta después de las rutas de la API, así que `/jobs`, `/health`,
`/entity-types` y `/docs` siguen contestando lo de siempre.

| Fichero | Qué hace |
|---|---|
| `js/api.js` | **El único que habla con el servidor.** Direcciones relativas, para que un prefijo de ruta tras el proxy no rompa nada |
| `js/texts/es.js` | **Todas las frases que ve el usuario**, incluidas las de error |
| `js/texts/index.js` | Elige el idioma (`?idioma=va`, o el último elegido) y rellena con castellano lo que falte traducido |
| `js/errores.js` | De código estable de la API a frase que dice qué ha pasado y qué hacer |
| `js/categorias.js` | Agrupación de los 30 tipos; lo que no tenga grupo cae en «Otros» y nunca queda invisible |
| `js/seguimiento.js` | Subida en fila, consultas periódicas con reintentos y avisos de página |
| `js/almacen.js` | Lo que recuerda el navegador: identificadores, nunca el nombre del fichero |

**Para añadir el valenciano** basta con crear `js/texts/va.js` con las mismas claves que `es.js` y
registrarlo en `js/texts/index.js`: no hay que tocar ninguna pantalla, y lo que falte traducido sale
en castellano en vez de quedar en blanco.

`python scripts/probar_interfaz.py` recorre los flujos en un navegador real contra el servicio
levantado y guarda capturas (necesita `playwright`). Las capturas de las pantallas clave están en
`docs/interfaz/`.

---

## API

Documentación interactiva en `/docs` con la aplicación arrancada.

| Método y ruta | Qué hace |
|---|---|
| `GET /entity-types` | Catálogo de tipos: `id`, `label`, `description`, `default_enabled` |
| `POST /jobs` | Encola un PDF. Responde **202** al terminar la subida, con `Location: /jobs/{id}`. No admite `include_entities` |
| `GET /jobs/{id}` | Estado, posición en cola, avance por páginas, resumen del resultado o error |
| `GET /jobs/{id}/result?output=pdf\|zip` | Descarga el PDF anonimizado (o PDF + `report.json` en ZIP) |
| `GET /jobs/{id}/report` | Informe completo en JSON |
| `POST /jobs/{id}/stop` | **Parar y entregar** lo procesado: **202**; **409** si aún está en cola o ya terminó |
| `DELETE /jobs/{id}` | **Eliminar** sin entregar nada: **204** si es inmediato, **202** si se está procesando |
| `POST /anonymize` | Camino síncrono: procesa en la propia petición. Admite PDF, PNG, JPG y TIFF |
| `GET /health` | Señal de vida (siempre 200) con GPU, modelos, cola, límites y problemas detectados |
| `GET /health/ready` | Igual, pero responde **503** si falta algo esencial (spaCy, EasyOCR) o si **el hilo que atiende la cola no está vivo** |

### Trabajos asíncronos

Es el camino pensado para la interfaz y para expedientes grandes.

```bash
# 1. Subir
curl -i -X POST "http://localhost:8000/jobs?disabled_types=DATE_TIME&disabled_types=URL" \
     -F "file=@expediente.pdf"
#   HTTP/1.1 202 Accepted
#   Location: /jobs/3f2a…

# 2. Consultar hasta completed / failed
curl http://localhost:8000/jobs/3f2a…
#   {"status": "running", "progress": {"pages_done": 12, "pages_total": 40, "percent": 30}, …}

# 3. Descargar
curl -o anonimizado.pdf "http://localhost:8000/jobs/3f2a…/result?output=pdf"
```

- **Estados**: `queued` → `running` → `completed` | `failed`; y `expired` cuando se ha borrado.
- **Avisos en vivo**: `notices` lleva, según van ocurriendo, las páginas que se han tachado
  enteras porque el OCR no pudo leerlas (`kind: page_redacted_in_full`, `reason: ocr_unreadable`
  o `ocr_timeout`), con la numeración que ve el usuario. Se corta en 200 avisos y `notices_total`
  dice cuántos ha habido; el informe final lleva la lista completa en `pages_redacted_in_full`.
  `kind` y `reason` son códigos estables: la frase la elige el cliente.
- **Errores al encolar**: 415 si no es PDF, 413 si supera `MAX_UPLOAD_MB`, 400 si el fichero no es
  un PDF válido, 422 si `disabled_types` es inválido, y 503 con `Retry-After` si la cola está llena.
- **Al descargar**: 409 si el trabajo no ha terminado o ha fallado, 410 si ya ha caducado.
- **Dos verbos, dos intenciones.** `DELETE` **elimina**: para y borra todo, sin entregar nada.
  `POST /jobs/{id}/stop` **para y entrega** las páginas ya procesadas.
- En los dos casos, procesándose no se puede cortar a mitad de página sin dejar un hilo ocupando
  la GPU: responden **202**, el estado sigue siendo `running` con `stopping: true` y la parada se
  consuma en cuanto suelta la página en curso. En digital es inmediato; en escaneado, como mucho
  `OCR_PAGE_TIMEOUT_S`. Insistir responde lo mismo; sobre algo inexistente, 404.
- **El parcial contiene solo páginas completas.** Las no procesadas se omiten, no se pintan de
  negro: pintar negro sobre una página digital no borra su texto, seguiría seleccionable debajo.
  Lo dicen su última página (generada, sin nada del documento), el nombre de la descarga
  (`documento_anonimizado_parcial_1-34_de_118.pdf`) y el informe (`partial`, `pages`,
  `pages_total_original`). Pasa por la misma verificación final que un documento entero.
- **Parar sin ninguna página hecha** no entrega nada —un PDF de cero páginas no es válido—: el
  trabajo se elimina. En cola, **409** `nothing_processed_yet` y sigue esperando turno.
- **Un documento a la vez** (`MAX_CONCURRENT_DOCUMENTS=1`): es el límite de acceso a la GPU. El
  camino síncrono `POST /anonymize` comparte ese mismo turno.
- **No existe un listado de trabajos**, a propósito: el servicio no tiene control de acceso y un
  listado dejaría ver y descargar los documentos de otros. Hace falta conocer el identificador.

### `disabled_types`

Un valor por parámetro: `?disabled_types=DATE_TIME&disabled_types=URL`. Antes de validar se quitan
espacios, se pasa a mayúsculas y se ignoran los valores vacíos; una lista vacía equivale a no
enviarlo. Un tipo inexistente se rechaza con **422 sin procesar nada**. No se admiten valores
separados por comas.

### Retención y borrado

- **El original** se borra del disco en cuanto termina el proceso (salvo `STORE_ORIGINAL=true`).
- **El resultado** se conserva `RESULT_TTL_MINUTES` (**360**, seis horas). Un barrido periódico lo
  borra del disco al caducar, lo consulte alguien o no. Seis horas y no una porque el documento
  anonimizado ya no lleva datos personales y porque una tanda larga caducaba por delante mientras
  aún se procesaba el final. Los parciales de `stop` se conservan lo mismo.
- **Los trabajos viven en memoria** del proceso y **no sobreviven a un reinicio**: los que estaban
  en cola o en curso se pierden y hay que volver a subirlos. Es una decisión consciente, para no
  operar un broker externo en un único nodo con una única GPU.
- **Al arrancar se borra lo que quedó de ejecuciones anteriores** en `TEMP_ROOT`. Tras un reinicio
  esos documentos son inalcanzables por la API —su identificador murió con el proceso— pero siguen
  en disco con datos personales dentro. En el registro consta cuántas carpetas se han borrado,
  nunca sus nombres. Presupone que `TEMP_ROOT` es de este servicio y de un solo proceso.
- **El informe de un trabajo nunca lleva los valores detectados.** `POST /jobs` no admite
  `include_entities`: el informe se conserva junto al resultado, y con los valores dentro sería
  conservar lo que se acaba de tapar. Quien los necesita para evaluar usa `POST /anonymize`, que
  no retiene nada. Los avisos de verificación dicen qué campo quedó sin limpiar y cuánto ocupa,
  nunca su contenido.
- **El servidor no conoce el nombre del fichero subido.** No se guarda, no aparece en el informe y
  la descarga se sirve como `documento_anonimizado.pdf`. Un nombre de expediente suele llevar el
  del interesado, y aquí sobreviviría al documento durante toda la retención; quien descarga desde
  la interfaz le pone el nombre que tiene en memoria.
- **Los registros no llevan texto del documento.** Un filtro sustituye DNI, NIE, CIF, correo,
  teléfono e IBAN en todo lo que se escribe, pero es una red de seguridad: un nombre o una
  dirección no tienen forma reconocible, así que la regla es no registrar texto. `LOG_PII=true`
  levanta esa regla solo para depurar.

### Autenticación y proxy inverso

**El servicio no tiene autenticación**, por decisión del proyecto: la protección depende del
aislamiento de red del despliegue. La aplicación no guarda estado de sesión, así que se puede añadir
autenticación en el proxy inverso sin tocar código. Tras un proxy, `ROOT_PATH` fija el prefijo de
ruta, las cabeceras `Location` son relativas y uvicorn arranca con `--proxy-headers` para respetar
`X-Forwarded-Proto`.

---

## Configuración

Por variables de entorno o fichero `.env` (plantilla en `.env.example`). Las más relevantes:

| Variable | Por defecto | Para qué |
|---|---|---|
| `MAX_UPLOAD_MB` | `200` | Tamaño máximo de subida |
| `MAX_PDF_PAGES` | `300` | Páginas máximas por documento |
| `MAX_CONCURRENT_DOCUMENTS` | `1` | Documentos procesados a la vez (límite de GPU) |
| `JOB_QUEUE_MAX` | `20` | Trabajos en cola antes de responder 503 |
| `RESULT_TTL_MINUTES` | `360` | Minutos que se conserva el resultado (6 h) |
| `JOB_SWEEP_INTERVAL_S` | `60` | Cada cuánto se borran los trabajos caducados |
| `STORE_ORIGINAL` | `false` | Conservar el original tras procesar |
| `ROOT_PATH` | *(vacío)* | Prefijo de ruta tras un proxy inverso |
| `TEMP_ROOT` | `/tmp/anonymizer` | Carpeta de trabajo |
| `LOG_PII` | `false` | Registrar texto del documento al depurar. Nunca activar en producción |
| `OCR_ENGINE` | `auto` | `auto` (MinerU y, si falta, EasyOCR), `mineru` o `easyocr` |
| `OCR_USE_GPU` | `true` | OCR en GPU |
| `OCR_MINERU_MODEL_PATH` | `model/MinerU2.5-Pro-2604-1.2B` | Pesos locales de MinerU |
| `EASYOCR_MODEL_PATH` | *(vacío = `~/.EasyOCR/model`)* | Pesos locales de EasyOCR |
| `EASYOCR_ALLOW_DOWNLOAD` | `true` | Permitir que EasyOCR descargue pesos (`false` sin salida a internet) |
| `OCR_PDF_RENDER_DPI` | `200` | Resolución de rasterizado de páginas escaneadas |
| `OCR_PAGE_TIMEOUT_S` | `360` | Tiempo máximo de OCR por página |
| `REDACT_UNREADABLE_PAGES` | `true` | Tachar entera la página ilegible o que agota el tiempo |
| `OCR_AUTO_ROTATE_ENABLED` | `true` | Corregir escaneos girados |
| `NER_ENGINE` | `spacy` | Motor de NER (`transformers` existe, pero empeoraba `PERSON` y el valenciano) |
| `NER_USE_GPU` | `true` | NER en GPU |
| `PRESIDIO_SCORE_THRESHOLD` | `0.55` | Puntuación mínima de una detección |
| `GAZETTEER_ADDRESS` / `GAZETTEER_PERSON` | `true` | Usar los gazetteers del INE |
| `REDACT_CASE_FILE_NUMBER` | `false` | Tachar el número de expediente administrativo (el número de salida se tacha siempre) |
| `FULL_REDACTION_ENABLED` | `true` | Página completa para copias de DNI y autoliquidaciones |
| `DNI_CROP_ENABLED` | `true` | En copias de DNI escaneadas, intentar tachar solo la tarjeta |
| `PLAN_PAGE_ENABLED` | `true` | Tratamiento de planos de obra por cajetín |
| `DETECT_VISUAL_SIGNATURES` / `DETECT_VISUAL_STAMPS` | `false` | Firmas y sellos por visión (desactivados: tachaban de más) |
| `VERIFY_OCR_LAYER_ENABLED` | `false` | Capa de verificación por OCR (duplica el tiempo) |

El resto de umbrales (señales de página completa, recorte del DNI, detección de planos) están
documentados junto a su definición en `app/core/config.py`.

---

## Ejecución en local

Requiere **Python 3.12** y, para el OCR, una GPU NVIDIA con CUDA. Sin GPU funciona en CPU, pero los
escaneados son muy lentos.

```bash
python -m venv .venv
source .venv/bin/activate                 # Windows: .venv\Scripts\activate

# PyTorch con CUDA primero (versión usada en desarrollo: 2.11.0+cu128)
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128

pip install -e ".[test,mineru]"
python -m spacy download es_core_news_md

# Pesos de MinerU (≈2,2 GB)
hf download opendatalab/MinerU2.5-Pro-2604-1.2B --local-dir model/MinerU2.5-Pro-2604-1.2B

cp .env.example .env
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

EasyOCR descarga sus pesos en el primer uso si `EASYOCR_ALLOW_DOWNLOAD=true`. `GET /health` dice si
falta algún modelo y si la GPU está disponible.

**Tests** (unos 4 minutos; no necesitan la API arrancada):

```bash
python -m pytest -q
```

---

## Evaluación

La evaluación usa un corpus sintético con verdad de referencia, generado por el proyecto hermano
`../corpus-generator`:

- **Principal**: 400 documentos administrativos (acuerdos, certificados, contratos, nóminas,
  notificaciones, resoluciones, formularios…), digitales y escaneados, en castellano y valenciano.
  Carpetas `output/pdfs` y `output/labels`.
- **Extensión**: 90 documentos de los tipos más difíciles —presupuestos de obra, copias de DNI y
  autoliquidaciones—, limpios, escaneados y escaneados girados. Carpetas `output/ext_pdfs` y
  `output/ext_labels`.

Con la API arrancada:

```bash
# Corpus principal
python -m evaluation.run --input-pdfs ../corpus-generator/output/pdfs \
    --input-labels ../corpus-generator/output/labels --workers 2 \
    --results-json evaluation/results.json --output evaluation/report.html

# Extensión: un solo trabajador y más tiempo de espera (los girados pasan de 180 s)
python -m evaluation.run --input-pdfs ../corpus-generator/output/ext_pdfs \
    --input-labels ../corpus-generator/output/ext_labels --workers 1 --timeout 900 \
    --results-json evaluation/results_ext.json --output evaluation/report_ext.html
```

Herramientas para comparar (no llaman a la API):

| Herramienta | Uso |
|---|---|
| `python -m evaluation.comparar_eval REF NUEVA` | Métricas globales y por tipo entre dos corridas; marca los tipos que se mueven |
| `python -m evaluation.comparar_documentos REF NUEVA` | Qué FN/FP aparecen o desaparecen, por documento y por grupo (tipo de documento × calidad) |
| `python -m evaluation.guardar_ref --pdfs DIR --out evaluation/work_ref` | Guarda la salida actual (texto y recuentos) antes de un refactor |
| `python -m evaluation.identidad_contra --pdfs DIR --ref evaluation/work_ref` | Comprueba que la salida no ha cambiado; sale con código 1 si hay diferencias |
| `python -m evaluation.compuerta_tipos REF PRUEBA --desmarcados T1,T2` | Compuerta: desmarcar un tipo no puede hacer desaparecer otro (ver su docstring) |
| `python -m evaluation.rebuild_report RESULTADOS.json INFORME.html` | Regenera el HTML de un informe desde su JSON |

Los resultados de referencia están en `evaluation/reference/` (bajo seguimiento). Todo lo demás que
genera la evaluación (JSON, HTML, logs) está ignorado por git.

---

## Métricas actuales

Corrida de referencia del **15 de septiembre de 2026**, con el código del commit `14d001d`, sobre el
corpus sintético descrito en [Evaluación](#evaluación). Los resultados completos están en
`evaluation/reference/results_main.json` y `results_ext.json`; una corrida nueva se compara con
`python -m evaluation.comparar_eval`.

Una entidad cuenta como acierto si el texto detectado coincide exactamente o se solapa al menos un
50 % con el anotado. `LOCATION`, `PROFESSIONAL_LICENSE` y `ROLE_TABLE_ROW` quedan fuera de la
métrica global.

### Corpus principal (400 documentos)

| Precisión | Recall | F1 | Aciertos | Falsos negativos | Falsos positivos | Errores de API |
|---|---|---|---|---|---|---|
| 0.920 | 0.929 | **0.924** | 9981 | 768 | 873 | 0 |

| Tipo | Precisión | Recall | F1 | Anotadas |
|---|---|---|---|---|
| `CASE_NUMBER` | 1.000 | 0.998 | 0.999 | 1108 |
| `IBAN_CODE` | 0.990 | 0.997 | 0.993 | 297 |
| `ES_DNI` | 0.991 | 0.987 | 0.989 | 765 |
| `EMAIL_ADDRESS` | 0.990 | 0.987 | 0.988 | 387 |
| `ES_NSS` | 0.987 | 0.987 | 0.987 | 374 |
| `DATE_TIME` | 0.953 | 0.999 | 0.975 | 1609 |
| `ES_NIE` | 0.979 | 0.963 | 0.971 | 246 |
| `ES_CIF` | 1.000 | 0.941 | 0.970 | 119 |
| `URL` | 1.000 | 0.918 | 0.957 | 391 |
| `DATE_OF_BIRTH` | 0.976 | 0.927 | 0.951 | 400 |
| `PASSPORT` | 0.923 | 0.947 | 0.935 | 114 |
| `QR_CODE` | 1.000 | 0.877 | 0.934 | 333 |
| `IP_ADDRESS` | 1.000 | 0.865 | 0.928 | 141 |
| `POSTAL_CODE` | 0.941 | 0.874 | 0.906 | 657 |
| `LICENSE_PLATE` | 0.918 | 0.889 | 0.903 | 63 |
| `MONEY` | 0.905 | 0.838 | 0.870 | 500 |
| `ADDRESS` | 0.881 | 0.845 | 0.863 | 659 |
| `PERSON` | 0.779 | 0.962 | 0.861 | 1936 |
| `PHONE_NUMBER` | 0.846 | 0.822 | 0.834 | 428 |
| `ORGANIZATION` | 1.000 | 0.409 | 0.581 | 22 |
| `CSV` | 1.000 | 0.175 | 0.298 | 200 |

### Extensión (90 documentos)

Presupuestos de obra, copias de DNI y autoliquidaciones, limpios, escaneados y escaneados girados.

| Precisión | Recall | F1 | Aciertos | Falsos negativos | Falsos positivos | Errores de API |
|---|---|---|---|---|---|---|
| 0.915 | 0.792 | **0.849** | 636 | 167 | 59 | 0 |

| Tipo | Precisión | Recall | F1 | Anotadas |
|---|---|---|---|---|
| `DATE_TIME` | 1.000 | 1.000 | 1.000 | 90 |
| `IBAN_CODE` | 1.000 | 1.000 | 1.000 | 32 |
| `MONEY` | 1.000 | 1.000 | 1.000 | 128 |
| `ES_NIE` | 1.000 | 1.000 | 1.000 | 5 |
| `DATE_OF_BIRTH` | 1.000 | 1.000 | 1.000 | 18 |
| `PHONE_NUMBER` | 1.000 | 0.906 | 0.951 | 32 |
| `ES_DNI` | 1.000 | 0.804 | 0.891 | 56 |
| `ORGANIZATION` | 1.000 | 0.750 | 0.857 | 40 |
| `ADDRESS` | 0.837 | 0.837 | 0.837 | 43 |
| `PERSON` | 0.750 | 0.867 | 0.804 | 180 |
| `CASE_NUMBER` | 1.000 | 0.385 | 0.556 | 104 |
| `POSTAL_CODE` | 1.000 | 0.360 | 0.529 | 75 |

Las autoliquidaciones se tachan enteras, y las copias de DNI enteras o por la tarjeta completa. En
esos documentos, un falso negativo del informe no deja el dato visible: solo significa que no se ha
contado.

---

## Limitaciones conocidas y pendientes

- **Corpus sintético.** Las métricas se miden sobre documentos generados; el rendimiento sobre
  expedientes reales puede variar, sobre todo en escaneados de mala calidad.
- **Tipos sin verdad de referencia.** `CADASTRAL_REFERENCE`, `CASE_TABLE_ROW`, `CERT_SERIAL`,
  `CREDIT_CARD`, `MEDICAL_ID`, `POLICY_NUMBER`, `REGISTRY_NUMBER`, `ROLE_TABLE_ROW` y `USER_ID` no
  tienen ninguna anotación en ninguno de los dos corpus: se detectan, pero su calidad no está medida.
  La extensión, además, no anota correo, CIF, NSS, IP, matrícula, pasaporte, QR ni URL.
- **Tipos con recall bajo.** `CSV` (0,18) y `ORGANIZATION` (0,41) en el corpus principal;
  `CASE_NUMBER` (0,39) y `POSTAL_CODE` (0,36) en la extensión. Ver
  [Métricas actuales](#métricas-actuales).
- **Filtrado de `PERSON` por gazetteer, incompleto.** El gazetteer del INE refuerza nombres dudosos
  y descarta topónimos, pero no se aplica como filtro completo: parte de los falsos positivos de
  `PERSON` (frases institucionales, nombres de persona dentro de una dirección) siguen pasando.
- **Artefacto de métrica en `PHONE_NUMBER`.** Cuando el PDF tiene el número partido en varias líneas,
  se detecta y se tacha, pero el informe lo devuelve con saltos de línea («+34\n706\n206\n505»). El
  evaluador compara texto exacto y lo cuenta a la vez como falso negativo y falso positivo.
- **O1 — Fecha de plano mal leída.** En el cajetín de un plano, el OCR puede leer una fecha como
  «01703/2026». Pendiente de verificar con un plano real.
- **O3 — Análisis repetido en páginas a dos columnas.** Los trozos de zona se solapan y el mismo
  texto se analiza más de una vez. Los recuentos son correctos (se deduplica en el plan); el coste
  es tiempo de proceso.
- **O4 — Marcas de agua.** En algunos documentos con marca de agua (5 de 78 en el corpus) aparece un
  tachado gigante que tapa contenido no personal.
- **Escaneos girados más lentos.** Elegir el giro a resolución completa lleva unos 21 s de media
  por página girada; las páginas que no están giradas no pagan nada extra.
- **Plano sin cajetín detectable**: la página se entrega intacta, con aviso de revisión manual.
- **Firmas y sellos por visión**, desactivados: tachaban texto y logotipos legítimos.
- **Sin autenticación** y **trabajos en memoria** (se pierden al reiniciar): ver [API](#api).

---

## Documentos reales de prueba

Los documentos reales —DNI, expedientes, tomos del ayuntamiento— van **siempre** en `samples/real/`,
que está ignorada por git. Un documento con datos personales que llega a un commit queda en el
historial aunque se borre después. Para partir un tomo grande en trozos:

```bash
python scripts/dividir_pdf.py samples/real/tomo.pdf --paginas 10 --salida samples/real/tomo_trozos
```

`scripts/debug_ocr_image.py` muestra qué lee el OCR en una imagen, para inspeccionar casos
concretos.
