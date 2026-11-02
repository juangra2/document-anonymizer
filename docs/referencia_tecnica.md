# Referencia técnica del anonimizador

El detalle para quien mantenga el código. La idea de cada parte —qué hace y por qué— está en la
[memoria](memoria.md); aquí, cómo está hecho, dónde y con qué reglas. Instalación y despliegue: el
[README](../README.md).

---

## Índice

1. [Catálogo de tipos y reglas](#catálogo-de-tipos-y-reglas)
2. [Detección](#detección)
3. [Recorrido de un documento](#recorrido-de-un-documento)
4. [Documentos de Word y Excel](#documentos-de-word-y-excel)
5. [Tratamientos especiales](#tratamientos-especiales)
6. [OCR](#ocr)
7. [Red de seguridad y verificación](#red-de-seguridad-y-verificación)
8. [Metadatos y contenido oculto](#metadatos-y-contenido-oculto)
9. [Retención, registro y acceso](#retención-registro-y-acceso)
10. [Interfaz web](#interfaz-web)
11. [API](#api)
12. [Evaluación](#evaluación)
13. [Despliegue](#despliegue)
14. [Documentos reales de prueba](#documentos-reales-de-prueba)

---

## Catálogo de tipos y reglas

31 tipos: 30 de texto más los códigos QR y de barras. `GET /entity-types` devuelve el catálogo
(`app/services/pii/entity_catalog.py`) con nombre legible, descripción y `default_enabled`. La
agrupación de la interfaz está en `app/web/js/categorias.js`; un tipo sin grupo cae en «Otros».

| Tipo | Qué captura |
|---|---|
| `PERSON` | Nombres y apellidos de personas físicas, incluidos los de firma («Fdo.: …») |
| `ES_DNI` | DNI/NIF, con o sin puntos (12345678A, 47.215.601-M) |
| `ES_NIE` | NIE de extranjero (X/Y/Z + 7 cifras + letra) |
| `ES_NSS` | Número de afiliación a la Seguridad Social |
| `PASSPORT` | Número de pasaporte |
| `MEDICAL_ID` | Historia clínica o tarjeta sanitaria |
| `PLACE_OF_BIRTH` | Lugar de nacimiento, solo tras su etiqueta |
| `IBAN_CODE` | Cuentas IBAN y cuentas antiguas de 20 dígitos (CCC) |
| `CREDIT_CARD` | Tarjeta de crédito o débito |
| `MONEY` | Importes ligados a una persona o cuenta (nóminas, transferencias, liquidaciones) |
| `BUDGET_AMOUNT` | Precios, mediciones y totales de un presupuesto de obra. Único tipo que viene desmarcado: sin `enabled_types`, el presupuesto queda a la vista |
| `POLICY_NUMBER` | Número de póliza de seguro |
| `ADDRESS` | Domicilios: tipo de vía, número, piso y municipio |
| `POSTAL_CODE` | Código postal |
| `PHONE_NUMBER` | Teléfonos fijos y móviles, formato español o internacional |
| `EMAIL_ADDRESS` | Correo electrónico |
| `CADASTRAL_REFERENCE` | Referencia catastral |
| `ORGANIZATION` | Razón social de empresa **privada** |
| `ES_CIF` | CIF de empresas y entidades |
| `CASE_NUMBER` | Referencias de expediente, también las municipales (`E-03001-2022-000074-00`) |
| `REGISTRY_NUMBER` | Número de registro de entrada o salida |
| `CSV` | Código seguro de verificación |
| `CERT_SERIAL` | Número de serie del certificado de firma electrónica |
| `LICENSE_PLATE` | Matrícula de vehículo |
| `QR_CODE` | Códigos QR y de barras |
| `DATE_OF_BIRTH` | Fecha de nacimiento |
| `DATE_TIME` | Cualquier otra fecha, incluidas las de registro y firma |
| `URL` | Enlaces y direcciones web |
| `IP_ADDRESS` | Direcciones IPv4 |
| `USER_ID` | Nombres de usuario e identificadores internos |
| `CASE_TABLE_ROW` | Filas de tabla que combinan expediente e interesado |
| `ROLE_TABLE_ROW` | Filas de tabla que asocian un cargo con una persona |

Reglas:

- **Valor por defecto único, el del catálogo**, en la interfaz y en la API. Cada petición apaga
  tipos con `disabled_types` y enciende los apagados con `enabled_types` (ver [API](#api)); hoy el
  catálogo trae todos activos, y `enabled_types` queda para el tipo que se decida apagar. Lo que
  queda apagado se escribe en `entity_types_disabled` del informe.
- **Número de colegiado** (`PROFESSIONAL_LICENSE`): se detecta pero nunca se tacha. No está en el
  catálogo. La regla se aplica en un único sitio, el plan de tachado.
- **`QR_CODE` desmarcado**: un código queda visible solo si se ha podido leer y su contenido no
  lleva ningún tipo que siga activo; uno ilegible se tacha igualmente. El QR de la sede lleva una
  URL y un CSV: conservarlo exige desmarcar también `URL` y `CSV`.
- **`LOCATION`** (municipios, provincias sueltos) se detecta para el filtro de direcciones, pero no
  se tacha ni está en el catálogo.

---

## Detección

`app/services/pii/analyzer.py` combina varias vías; todas producen entidades con tipo, posición y
puntuación, y los solapes se resuelven al final. Los tipos desactivados se quitan **antes** de
resolver solapes, para que un tipo apagado no oculte ni sustituya a otro activo.

- **Reconocedores con formato** (`recognizers_es.py`): DNI, NIE, CIF, NSS, IBAN, teléfonos,
  correos, matrículas, referencias catastrales… El dígito de control (DNI módulo 23, IBAN módulo
  97) cuenta como evidencia a favor, nunca como veto.
- **Campos con etiqueta**: la etiqueta («DNI:», «Nom i cognoms:», «Domicili:») decide el tipo del
  valor que la sigue (`pii/label_types.py`). Se reconocen las formas bilingües de los impresos de la
  GVA («NOM / NOMBRE», «COGNOMS / APELLIDOS») y el valor en la línea siguiente sin dos puntos. Un
  nombre de pila suelto tras «NOM»/«NOMBRE» en la misma línea también cuenta.
- **NER** (reconocimiento de entidades con nombre): spaCy `es_core_news_md` orquestado por
  Presidio, para nombres de persona y organizaciones. Un motor `transformers` existe
  (`NER_ENGINE=transformers`) pero empeoraba `PERSON` y el valenciano.
- **Gazetteers del INE** (`pii/gazetteer.py`, artefactos en `pii/gazetteers/`, se regeneran con
  `scripts/build_gazetteers.py` desde las descargas en `data/ine_raw/`): nombres, apellidos, municipios y provincias. Validan direcciones
  (código postal ↔ provincia ↔ municipio), refuerzan nombres dudosos y alimentan un detector de
  nombres en prosa. **Regla del padrón**: un nombre detectado sin dos puntos («NOMBRE / PEDRO»)
  tiene que llevar un nombre o un apellido del padrón; se midió que no descarta ningún nombre real.
- **Filtro institucional** (`pii/institution_filter.py`): el nombre de una institución pública no
  se tacha, ni el teléfono, la dirección o el correo que la acompañan en el membrete o el pie. El
  membrete y el pie se reconocen por posición, con cuidado de no tomar el pie por membrete en
  cartas cortas. Un dato de persona etiquetado («Domicilio fiscal», «Nombre y apellidos») nunca se
  aparta por institucional.
- **Filtros de contexto**: importes solo junto a persona o cuenta; razón social solo con señales de
  empresa privada; firmas cortadas en el cargo.
- **Tolerancia al OCR**:
  - un DNI o NIE cuya letra de control el OCR ha leído como cifra (8 por B, 5 por S, 6 por G…) se
    acepta tras una etiqueta de DNI si la cifra se parece a la letra que exige el control;
  - un IBAN con letras leídas como cifras (O por 0, S por 5…) se reconoce tras normalizarlo;
  - un IBAN o un teléfono partido en dos líneas se une.
- **Barrido**: en el camino digital, un dato detectado se tacha en **todas** sus apariciones del
  documento, también donde no se detectó por sí solo (trozos de direcciones partidas, el nombre sin
  la etiqueta).

---

## Recorrido de un documento

`app/services/pdf/pipeline.py` inspecciona el PDF (`inspector.py`) y lo envía por uno de tres
caminos:

| Documento | Módulo | Qué hace |
|---|---|---|
| Digital | `redactor.py` | Texto y posición de cada palabra con PyMuPDF; el tachado borra las palabras del contenido del PDF |
| Escaneado | `scanned_redactor.py` | Rasteriza cada página, OCR, tacha sobre la imagen y reconstruye el PDF con imágenes |
| Mixto | `hybrid_redactor.py` | Pasada digital de todo el documento y después el bucle escaneado solo para las páginas que lo necesitan |

Una página necesita OCR (`inspector.pagina_necesita_ocr`) si no tiene texto o si una imagen que no
es fondo cubre al menos la mitad. Las demás conservan su texto seleccionable; sus imágenes pegadas
se leen aparte. Las imágenes sueltas (JPEG, PNG, TIFF, también de varias páginas) se admiten en
la cola y en `POST /anonymize` y salen en su mismo formato (`services/image/imagen.py`): cada
página se mete en un PDF medido para que el camino escaneado la lea con sus píxeles (a
`OCR_PDF_RENDER_DPI`), pasa por ese camino completo y se saca de la salida, a su tamaño original si
el tope de megapíxeles la redujo. Antes se aplica el giro del EXIF. El formato se decide por el
contenido, no por la extensión; tope `MAX_IMAGE_MEGAPIXELS` (100). Con «solo quitar metadatos», un
JPEG derecho pierde sus segmentos de metadatos sin recomprimirse (conserva ICC y Adobe) y todo lo que
va detrás del final de la imagen: la segunda imagen de un JPEG de móvil (MPF), que es la misma foto,
o texto pegado (`limpieza._fin_de_imagen`, que recorre también los JPEG progresivos); uno girado
por EXIF se endereza y se recomprime; un PNG pierde texto, fecha y EXIF con los píxeles intactos;
un TIFF se reescribe sin etiquetas. `tests/test_imagenes.py`.

**Zonas.** Cada página se divide en zonas tipadas (`HEADER`, `FOOTER`, `BODY`, `FORM`, `TABLE`,
`SIGNATURE`, `QR_CODE`; `services/layout/`). Cada zona tiene su política en `zone_config.py`: qué
detectores corren y qué se tacha. En `HEADER`, por ejemplo, no se buscan teléfonos: son los de la
institución.

**Redacción estructural.** En formularios y tablas se reconoce la etiqueta y se tacha su valor con el
tipo que corresponde a la etiqueta. El valor se corta en el canal entre columnas, en la siguiente
etiqueta de la misma fila o en un salto grande, para no comerse el campo vecino
(`layout/value_split.py`). Una etiqueta de varias palabras solo corta tras un canal de columnas.
En páginas a dos columnas los trozos de zona se solapan y el mismo texto se analiza más de una vez;
los recuentos son correctos (el plan deduplica), pero cuesta tiempo.

**Plan de tachado** (`pdf/redaction_plan.py`). Todo lo que produce un rectángulo a tachar
—analizador, redacción estructural, firmas, QR, cajetín— lo da de alta en un plan por página. Nadie
pinta ni cuenta por su cuenta. El plan aplica en un único sitio el filtro de tipos desactivados y la
regla del colegiado, admite solo excepciones con nombre (página completa, QR con datos de tipos
activos) y deduplica. Los rectángulos que se pintan y las entidades del informe salen de la misma
lista.

**Cómo se borra.** En digital, cada rectángulo es una anotación de redacción de PyMuPDF; al
aplicarla (`apply_redactions`) se eliminan del contenido los caracteres que caen dentro y los
píxeles de las imágenes que cubre. Antes de aplicar, cada marca se ajusta en altura a las letras que
tapa, para no morder la etiqueta de encima; la marca que cubre una imagen no se ajusta. En
escaneado se pintan los píxeles de la imagen de la página y el PDF se reconstruye con esa imagen;
la barra del valor tampoco muerde el rótulo de encima.

---

## Documentos de Word y Excel

`app/services/office/` (`docx.py`, `xlsx.py`, `paquete.py`); el formato se reconoce por el contenido en
`services/formatos.py`, que es también el despacho único de la cola y de `POST /anonymize`.

- **Resultado**: un Word. No hay barras: cada dato pasa a ser un marcador con su tipo (`[DNI]`,
  `[NOMBRE]`, `[DIRECCIÓN]`…, en castellano también en un documento en valenciano), con el formato
  del tramo donde empezaba. Se trabaja sobre el XML de cada parte, no con python-docx, que no ve
  cuadros de texto, notas al pie, controles de contenido ni texto borrado con control de cambios.
- **Detección**: el texto de cada parte (cuerpo, cada cabecera y pie, notas) se analiza línea a línea
  en orden: un párrafo es una línea, una fila de tabla es una línea con las celdas separadas por
  tabuladores (así se leen rótulo y valor). Un dato no cruza de una celda a otra ni incluye la
  etiqueta que lo precede (el analizador devuelve a veces «de contacto: 611…»; se corta tras los
  últimos dos puntos seguidos de espacio). Después, cada valor detectado se sustituye en todo el
  documento donde aparezca.
- **Fuera de los párrafos** (`barrido.py`, común con Excel): el resto del texto de todas las partes
  pasa también por el analizador. En Word: nombre y etiqueta de los controles de contenido, texto de
  formas y marcas de agua, textos alternativos que queden. Los nombres de marcador los pone quien
  escribe («Ferrandis»): se renombran a `MarcadorN` (los que empiezan por «_» los crea Word).
- **Limpieza** (también en «solo quitar metadatos»): se aceptan los cambios (lo borrado se quita, lo
  insertado queda como texto); fuera comentarios, texto oculto, códigos de campo, enlaces (el texto
  se queda), textos alternativos, enlaces de datos de controles de contenido, identificadores de
  sesión (`rsid`), permisos con nombre, propiedades del documento y personalizadas, datos XML
  personalizados, miniatura, plantilla adjunta, variables, combinación de correspondencia,
  glosario y todas las relaciones externas. Los objetos incrustados (OLE, gráficos con sus datos)
  se quitan con el marcador `[OBJETO ELIMINADO]`.
- **Imágenes incrustadas**: JPEG y PNG pasan por el camino de imágenes (tachado dentro de la imagen,
  copia de DNI tapada entera, sin metadatos); las de otros formatos (EMF, WMF, GIF…), que no se
  pueden revisar, se quitan con `[IMAGEN ELIMINADA]`.
- **Comprobaciones al entregar**: el Word se vuelve a abrir (zip sano y python-docx) o no se entrega;
  el verificador (`paquete.verificar`) busca cada valor detectado en el texto, los atributos y las
  relaciones de todas las partes de la salida, y si queda alguno el informe pide revisión.
- **Segunda opinión** (`verificador.py`, común a Word y Excel): el verificador de valores no puede
  ver lo que no se detectó; este vuelve a leer la salida entera (cada párrafo o fila de Word, cada
  celda de Excel, también las no sustituidas, y el resto del texto de todas las partes, como el
  barrido) y la pasa por el analizador. Lo que encuentra no se toca: el informe pide revisión
  (`residual_pii`) y dice cuántos posibles datos y de qué tipo, nunca los valores. Releer suelto, sin
  el contexto del original, da nombres que no lo son («Ciutat Vella», «Vocal»): un nombre solo cuenta
  si tiene al menos dos palabras y empieza por un nombre de pila del padrón seguido de un apellido
  (o al revés, «Apellidos, Nombre»). En los cuatro corpus de Office solo marca datos que quedan de
  verdad.
- **Seguridad del paquete**: límites de partes, tamaño descomprimido y proporción de compresión;
  nombres de parte sin rutas absolutas ni `..`, y sin repetir (dos partes con el mismo nombre: se
  leería una y Word o Excel podrían mostrar la otra); XML sin entidades ni red; ninguna ruta de
  dentro del zip se usa para escribir en disco. Al subirlo, un Excel de más de `MAX_OFFICE_CELLS`
  celdas o un Word de más de `MAX_OFFICE_PARAGRAPHS` párrafos se rechaza (`office_too_large`, 413),
  contando en el XML sin interpretarlo. El tratamiento tiene límite de tiempo, como el OCR
  (`OFFICE_TIMEOUT_S`): si se pasa, el trabajo falla con `processing_timeout` y no se entrega nada.
  La clasificación de columnas es lineal y la segunda opinión lee las filas en bloques: una hoja de
  120 000 celdas tarda unos 35 s. Un `.doc`/`.xls` antiguo
  se rechaza con `legacy_office` y un `.docm`/`.xlsm` con `office_macros` (415), con su frase.
- **Informe en PDF**: sin mapa de páginas (un Word no tiene páginas fijas); dice «documento de
  Word» y lista lo quitado (cambios, comentarios, objetos incrustados…).

**Excel** (`xlsx.py`): la columna con datos personales conserva su cabecera y sus celdas llevan el
marcador del tipo, como en Word; no se borran columnas.

- **Cabecera**: la primera fila (de las veinte primeras) con al menos dos rótulos —texto corto, sin
  arrobas y casi sin cifras— y datos debajo; un título encima, de una sola celda, no cuenta. Si
  ninguna fila es así, la que más se parece a nombres de columna conocidos.
- **Clasificación de columnas**: por el nombre de la cabecera (`xlsx.CABECERAS`, castellano y
  valenciano: «DNI», «Nom i cognoms», «Data de naixement», «Correu electrònic»…) o, si no dice nada
  («Col3»), por el contenido: al menos el 60 % de sus valores son DNI o NIE con letra válida,
  correos, IBAN, teléfonos (también guardados como número) o nombres del padrón. Las fechas solo por
  la cabecera. Texto libre (observaciones, descripción, motivo… o valores largos con espacios): se
  analiza celda a celda y solo se sustituye el dato; lo mismo cualquier celda de texto suelta de 25
  caracteres o más.
- **Más formas de una columna personal**: abreviaturas y sinónimos de cabecera («Nº documento»,
  «D.N.I.», «F. Nac.»); teléfonos guardados como número con el 34 delante; la cuenta repartida en
  columnas (país, entidad, oficina, DC y cuenta son un solo dato: hace falta la columna de la cuenta
  y otra de entidad, oficina o DC, porque «Entidad» sola es un organismo); y el número de expediente
  (`CASE_NUMBER`, que se tacha por defecto). Una **ficha en vertical** (los rótulos en la columna A y
  cada persona en una columna) se sustituye por filas: hacen falta dos rótulos de tipos distintos que
  sean casi toda la columna A, para no confundirla con un listado con un título de grupo encima.
- **Importes** (la regla del PDF): una columna con cabecera de importe (`xlsx.IMPORTES`: importe,
  cuantía, ayuda, subvención, nómina, precio, medición…) y contenido numérico es `MONEY` si la hoja
  tiene alguna columna personal, y `BUDGET_AMOUNT` si la hoja es un presupuesto de obra (las palabras
  de `analyzer._BUDGET_VETO_RE` o una columna «Partida»). Sin personas ni presupuesto (una
  estadística por distritos), no se toca.
- **Casillas**: una columna cuyo tipo viene desmarcado no se sustituye, ni las fórmulas que la
  copian; el texto libre usa el analizador con las mismas casillas. Con las del catálogo, un
  presupuesto queda intacto.
- **Fórmulas**: la que trae un dato de una columna personal (`=A2`, `=LEFT(A2,1)`, `=MAX(C2:C9)`…)
  pasa a ser el marcador; las que solo cuentan (`COUNTA`, `ROWS`…) se quedan. `SUM` y `AVERAGE` de dos
  o más importes `MONEY` se quedan escritas con su valor (`=2748.3`), calculado con los importes de
  antes de sustituir: las celdas que sumaban ya llevan el marcador. Si alguno de esos importes sale
  de otra fórmula, se aplica la regla general. A todas se les quita el valor guardado y el libro se
  recalcula al abrirse (`fullCalcOnLoad`).
- **Fuera de las celdas** (`barrido.py`): todo el texto que no es el valor de una celda pasa por el
  analizador: listas desplegables, formato condicional, autofiltros, títulos de gráficos, nombres
  definidos, cuadros de texto. En las fórmulas (de celda, de regla, de validación, de nombre
  definido) se miran solo los textos entre comillas, juntos (`CONCATENATE("Vicenta"," Ribes")` es un
  nombre), y los números largos sueltos (un teléfono); las referencias no se tocan y la fórmula
  sigue siendo válida: `="Llamar a [NOMBRE] al "&"[TELÉFONO]"`. De los atributos, solo los que
  guardan texto del usuario (valor de un filtro, texto alternativo, título, mensaje de una
  validación…): cualquier otro puede ser el nombre de una fuente, y «Times New Roman» parece un
  nombre. Estilos, temas, fuentes, propiedades y relaciones no pasan por el barrido.
- **Lo que guarda aparte**: hojas ocultas (se tratan como las visibles); fuera comentarios (también
  encadenados y sus personas, con su dibujo VML), tablas dinámicas (definición y copia de datos; sus
  celdas quedan vacías con `[TABLA DINÁMICA ELIMINADA]`), copias de datos de los gráficos, vínculos a
  otros libros con su copia, conexiones y consultas, nombres definidos con un valor fijo,
  hipervínculos, el usuario que comparte el libro y las propiedades. Cabeceras y pies de página y
  cuadros de texto se analizan. Un nombre de hoja que es un dato personal pasa a «Hoja N» en todo
  el libro. La tabla de cadenas compartidas se rehace solo con las que siguen en uso.
- **Comprobaciones**: el libro se vuelve a abrir (zip y openpyxl) o no se entrega; el verificador
  busca cada valor sustituido en todas las partes, y la segunda opinión (la de Word) relee el libro
  entero. El informe lleva `replaced_columns` (hoja,
  cabecera, tipo, celdas) y el informe en PDF las lista.

---

## Tratamientos especiales

- **Página completa** (`pdf/full_redaction.py`): la copia de un DNI y el impreso de autoliquidación
  tributaria (modelos 046, 600, 620…) se tachan enteros. Se decide por señales textuales distintas
  con un mínimo configurable, página a página. En una copia de DNI escaneada se intenta tachar solo
  la tarjeta: se pinta la región, se vuelve a pasar OCR y, si queda texto legible fuera, se tacha la
  página entera. Si el documento entero se clasifica como copia o autoliquidación, la decisión se
  propaga a sus páginas.
- **Imágenes pegadas** (`pdf/imagenes_pegadas.py`): en una página con texto, cada imagen que cubre
  al menos el 1 % se lee recortada, con sus propios píxeles y probando los giros. Si es una copia de
  un documento de identidad, se tapa la imagen entera; si no, se tachan los datos leídos en ella.
  Después se relee el recorte tachado. Una imagen con el formulario escrito encima es un **fondo**
  (`pdf/fondos.py`: al menos seis líneas de texto repartidas sobre ella, dibujadas después), no una
  copia; un sello sobre una copia no la convierte en fondo. Una firma pegada no se tapa entera.
- **Planos de obra** (`pdf/plan_pages.py`, `vision/titleblock.py`): páginas A2 o mayores, sin
  párrafos y con mucho dibujo, detectadas antes de rasterizar. Se localiza el cajetín y se analiza
  solo ese recorte; el verificador también mira solo el cajetín (en el dibujo, los rótulos parecen
  nombres y las cotas pasaportes), y el informe lo dice. Sin cajetín, la página queda intacta y
  marcada para revisión. De la salida solo se quita el texto diminuto que no se ve. Una imagen
  suelta nunca es un plano: el PDF que arma la vía de imágenes lleva la marca
  `MARCA_IMAGEN_SUELTA`, porque su tamaño de página sale de los píxeles (una foto de 60 MP daba
  «gran formato» y salía sin tachar).
- **Códigos QR y de barras** (`vision/code_detector.py`): se localizan y se decodifican con zbar,
  OpenCV y zxing; se tachan según la regla del catálogo. Tope de tamaño: un código que se
  descodifica puede ocupar hasta el 35 % de la página (el QR de un carnet es el 8-15 % de la
  tarjeta); lo que no se descodifica sigue limitado al 5 %, que evita tapar sellos y diagramas.
- **Firmas y sellos por visión**: existen (`DETECT_VISUAL_SIGNATURES`, `DETECT_VISUAL_STAMPS`) pero
  están desactivados: tachaban texto y logotipos legítimos.
- **Páginas giradas** (`ocr/orientation.py`): si el OCR sale como ruido (pocos caracteres por
  token), se prueban 90°, 270° y 180° a resolución completa y se elige por longitud de token y
  confianza. Una página girada lleva unos 21 s más de media.
- **Antes de dar una página por ilegible** (`ocr/ajustes.py`, `OCR_AJUSTES_ENABLED`): si ni girada
  se lee, se prueban tres ajustes y gana la lectura legible que más lee: enderezar una inclinación
  de 0,7° a 20° (ángulo de las líneas por Hough), recortar la hoja del fondo y llevarla a un
  rectángulo (perspectiva), e invertir los colores si la imagen es oscura (texto claro sobre fondo
  oscuro; la inversión es solo para leer). Un ajuste geométrico solo vale si el texto queda derecho
  (menos de 2° de inclinación residual): un recorte de hoja mal detectado deja el texto torcido y las
  barras caerían desplazadas. La imagen ajustada no pasa de `OCR_MAX_MEGAPIXELS`. Se trabaja en el
  espacio ajustado (OCR, zonas, barras, segunda y tercera lectura) y lo tapado vuelve a la imagen
  original con la matriz inversa; con la hoja recortada, lo de fuera se lee aparte y se tapa lo que
  lleve datos. Una página que hoy se lee bien no pasa por nada de esto.
- **Legible** (`orientation.legible`): muchos caracteres por pieza (`OCR_MIN_CHARS_PER_TOKEN`), o al
  menos 5 piezas leídas con confianza media de 0,70 y al menos un 60 % de palabras (tres letras o
  cifras). Una foto inclinada o un carnet salen palabra a palabra y se leen bien; el ruido de una
  página girada da piezas de uno o dos signos aunque el OCR las puntúe alto.
- **Páginas ilegibles**: una página escaneada que el OCR no puede leer en ningún giro ni con ningún
  ajuste, o que agota `OCR_PAGE_TIMEOUT_S`, se tacha entera (`REDACT_UNREADABLE_PAGES`). La
  pantalla, el informe y el informe en PDF dicen por qué, por su motivo (`ocr_unreadable`,
  `ocr_timeout`, `dni_scan`, `tax_form`).
- **Barra al tramo del dato** (`scanned_redactor._entity_rects_via_easy`): cuando EasyOCR devuelve
  una línea entera como una pieza («NOMBRE COMPLETO: Alejandro Martín Gómez»), la barra se recorta
  al dato aunque sea mayoría. Primero se relee la pieza palabra a palabra (`EasyOcrEngine.palabras`)
  y se toma el tramo seguido de palabras del dato que lo cubre entero, el de más a la derecha si hay
  varios (la etiqueta puede repetir una palabra del valor), con al menos el 90 % de sus caracteres y
  absorbiendo las piezas de una o dos letras pegadas detrás (la letra de un DNI). Si el detector
  pega la etiqueta a la primera palabra del valor («DIRECCIÓN:Avenida»), se parte por el hueco de
  tinta. Sin relectura válida, la estimación por anchura de carácter se ajusta a los huecos de tinta
  entre palabras hacia el lado prudente: el comienzo solo retrocede y el final solo avanza, así que
  si se desvía se come parte de la etiqueta, nunca del dato. Un trozo de dato pegado al borde de una
  pieza (un DNI partido entre dos líneas del OCR) se tapa hasta ese borde.

---

## OCR

- **MinerU 2.5-Pro** (`ocr/mineru_engine.py`): modelo de visión y lenguaje de 1.200 millones de
  parámetros, en GPU. Lee la página con orden de lectura y estructura (párrafos, tablas); la
  detección corre sobre su texto. No se le pide el contenido de los bloques de imagen (disparaba
  transcripciones de decenas de minutos) y se limita la longitud de lo que genera por bloque. Las
  celdas donde pega rótulo y valor se despegan.
- **EasyOCR** (`ocr/easyocr_engine.py`): da la caja de cada palabra para situar los tachados, hace
  la segunda pasada sobre la imagen ya tachada y sustituye a MinerU si no está. Tras cada lectura se
  devuelve a la GPU la memoria que PyTorch se guarda: sobre una página de 8 megapíxeles el detector
  reserva varios GB y, con MinerU cargado, la siguiente lectura (otro giro, una foto enderezada) no
  cabía en 8 GB (en Linux falla; en Windows desborda a la memoria del sistema).
- La decisión de dispositivo es única (`core/dispositivo.py`): GPU si se pide y CUDA está
  disponible.

---

## Red de seguridad y verificación

- **Segunda pasada** (camino escaneado y páginas con OCR del híbrido): tras tachar, EasyOCR vuelve
  a leer la imagen. Lo que aún se lee de un tipo activo se tapa antes de entregar (la «red de
  seguridad»). Si tras la red sigue legible un DNI, NIE, IBAN o CIF, el informe pide revisión
  (`safety_net_still_readable`). Desactivarla (`OCR_SECOND_PASS_VERIFICATION=false`) deja un aviso
  en el registro al arrancar.
- **Verificación final** (`services/verification/`, caminos digital e híbrido): capas sobre el PDF
  entregado. La textual vuelve a analizar el texto extraíble de cada página con los mismos filtros
  que el tratamiento; la de metadatos, ver la sección siguiente; la binaria busca datos con formato
  en los bytes del fichero (sin tomar por datos los flujos comprimidos ni las cadenas
  hexadecimales); la de OCR es opcional (`VERIFY_OCR_LAYER_ENABLED`). Los documentos muy largos se
  verifican por trozos, y las páginas que no se pudieron verificar se avisan.
- **Avisos con código** en el informe (`coded_warnings`): `plan_titleblock_only_verified`,
  `pages_not_verified`, `safety_net_acted`, `safety_net_still_readable`, `content_not_reviewed`
  (solo se quitaron metadatos) y `digital_signature_invalidated` (la entrada llevaba firma digital,
  en los dos modos). Los motivos de revisión
  manual van en `manual_review_reasons`.

---

## Metadatos y contenido oculto

Todo en `app/services/pdf/limpieza.py`, en dos momentos.

**Antes del análisis**, al abrir el documento (caminos digital y escaneado):

- las anotaciones que se ven (texto libre, sellos, dibujos, subrayados) y los campos de formulario
  pasan a ser contenido de la página (`Document.bake` de PyMuPDF). Su texto se analiza y se tacha
  como el resto, y un rótulo pintado dentro de un campo se conserva. El documento deja de ser un
  formulario;
- lo que no se ve se quita sin integrarlo: notas de comentario con su autor, adjuntos, enlaces,
  sonidos y anotaciones ocultas. El valor de un campo que no está dibujado se pierde.

**Al guardar**, en todos los caminos (también páginas de plano y páginas copiadas por el híbrido):

- fuera las anotaciones y campos que queden, los marcadores, el JavaScript (acción de apertura,
  árbol de nombres, acciones), los adjuntos y su árbol, las miniaturas, el XMP del documento, de
  cada página y de cada imagen, y el diccionario de información (también el `/Info` que MuPDF deja
  en el catálogo);
- fuera las copias de texto: la estructura de etiquetas y el `/ActualText`, `/Alt` y `/E` del
  contenido marcado. El tachado quita los glifos, pero no esas copias: un lector de pantalla o
  copiar el texto devolverían el dato tachado;
- los JPEG que se conservan pierden EXIF, GPS, XMP, IPTC, JFIF, comentarios y marcas del
  codificador **sin recomprimirse**: se quitan esos segmentos y los datos de imagen quedan byte a
  byte iguales. Se conservan el perfil de color (ICC) y la marca de Adobe, que en CMYK decide los
  colores;
- el fichero se escribe entero (`incremental=False`, `garbage=4`), sin versiones anteriores.

| Qué | Digital | Escaneado | Híbrido | Imagen suelta |
|---|---|---|---|---|
| Diccionario de información | fuera | fuera | fuera | fuera |
| XMP de documento, página e imagen | fuera | fuera | fuera | fuera |
| EXIF, GPS, XMP, IPTC de las imágenes | fuera, sin recomprimir | se repinta | fuera / se repinta | se repinta; sale sin ellos |
| Versiones anteriores | fuera | fuera | fuera | — |
| Notas de comentario, adjuntos, enlaces | fuera | fuera | fuera | — |
| Texto libre, sellos, dibujos, campos | a la página y analizados | ídem (OCR) | ídem | — |
| Marcadores, JavaScript, miniaturas | fuera | fuera | fuera | — |
| Etiquetas y copias de texto | fuera | se repinta | fuera | — |

**Verificación.** La capa de metadatos del verificador comprueba todo lo anterior en la salida (sin
copiar valores a los avisos; `format` no cuenta, es la versión del PDF). La comprobación
independiente es `python -m evaluation.comprobar_metadatos SALIDA.pdf`: solo ExifTool y qpdf, sin
código del servicio; imprime nombres de campo y recuentos, nunca valores; código 1 si queda algo;
busca las herramientas en `EXIFTOOL`/`QPDF` o en el PATH y se salta si faltan.
`tests/test_comprobar_metadatos.py` la pasa sobre cuatro casos cargados de metadatos
(`tests/casos_metadatos.py`: PDF digital con toda la carga y una foto de móvil pegada; página que es
solo una foto; documento mixto; guardado incremental con una versión anterior sin tachar), por los
tres caminos.

**Inventario.** `inventario_metadatos` cuenta, antes de tocar la entrada y sin cambiarla, lo que
lleva fuera del contenido visible; va al informe como `metadata_removed` (solo recuentos por
categoría: `document_info`, `xmp`, `comments`, `form_fields`, `bookmarks`, `javascript`,
`attachments`, `photo_metadata`, `thumbnails`, `links`, `accessibility_tags`, `text_copies`,
`previous_versions` y, en el modo siguiente, `hidden_text`). Los campos de firma (`/FT /Sig`)
dan el aviso `digital_signature_invalidated`.

**Solo quitar metadatos** (`app/services/pdf/solo_metadatos.py`, `treatment=metadata_only`): no se
analiza ni se tacha. Se eliminan todas las anotaciones que no son campos (los comentarios no se
integran: nadie revisa lo que lleven), los campos pasan a la página (`bake`), se quita el texto
que no se ve de cualquier tamaño (modo invisible o transparente, tapado por una imagen dibujada
después, o blanco sobre una zona que se pinta blanca; un trozo que pisa texto visible se deja) y
después el mismo `scrub` y `limpiar_para_entregar` que al anonimizar. La capa de texto de un
escaneado con OCR es texto invisible: se quita y el PDF deja de poder buscarse. Sin GPU; pasa por
la misma cola. El informe lleva `treatment: metadata_only`, el aviso `content_not_reviewed` y la
descarga se llama `documento_sin_metadatos.pdf`
(`tests/test_solo_metadatos.py`, con los mismos casos cargados y ExifTool y qpdf).

No se limpian: JPEG 2000, JPEG bajo otro filtro, imágenes en línea en el contenido. Las capas
opcionales desactivadas no se han comprobado.

---

## Retención, registro y acceso

- **Original**: se borra en cuanto termina el proceso (salvo `STORE_ORIGINAL=true`).
- **Resultado**: se conserva `RESULT_TTL_MINUTES` (360). Un barrido cada `JOB_SWEEP_INTERVAL_S` lo
  borra al caducar. Seis horas y no una porque el resultado ya no lleva datos personales y una
  tanda larga caducaba por delante mientras aún se procesaba el final.
- **Trabajos en memoria del proceso**: no sobreviven a un reinicio. Decisión consciente: un único
  nodo con una única GPU no justifica operar una cola externa.
- **Limpieza al arrancar**: se borra lo que quedó en `TEMP_ROOT` de ejecuciones anteriores, solo
  de procesos que ya no existen (`services/storage/propiedad.py`); el registro dice cuántas
  carpetas, nunca sus nombres. Antes, el arranque comprueba que puede escribir en `TEMP_ROOT`.
- **Informe sin valores**: `POST /jobs` no admite `include_entities`; el informe se conserva junto
  al resultado y con los valores sería conservar lo tapado. `POST /anonymize`, que no retiene nada,
  sí lo admite (lo usa la evaluación).
- **Nombre del fichero**: el servidor no lo guarda; la descarga se sirve como
  `documento_anonimizado.pdf` y la interfaz le pone el nombre que tiene en memoria.
- **Registro** (`core/logging.py`): un filtro en los manejadores tacha DNI, NIE, CIF, correo,
  teléfono e IBAN, argumento a argumento (sin romper el registro de accesos de uvicorn). Es una
  red: la regla es no registrar texto del documento. `LOG_PII=true` la levanta para depurar y deja
  un aviso al arrancar.
- **Sin autenticación**: la protección es el aislamiento de red o un proxy inverso que la ponga. La
  aplicación no guarda sesión. Tras un proxy, `ROOT_PATH` fija el prefijo, las cabeceras `Location`
  son relativas y uvicorn arranca con `--proxy-headers`.
- **Sin listado de trabajos**: sin control de acceso, un listado dejaría ver los documentos de
  otros. Hace falta conocer el identificador.

---

## Interfaz web

En `app/web/`, servida por la propia aplicación en `/`, después de las rutas de la API. HTML, CSS y
módulos de JavaScript sin compilación, sin Node y sin pedir nada a internet. Los ficheros se sirven
con `Cache-Control: no-cache` para que una actualización no mezcle HTML nuevo con CSS viejo.

| Fichero | Qué hace |
|---|---|
| `js/api.js` | El único que habla con el servidor. Direcciones relativas, para no romperse tras un prefijo de proxy |
| `js/texts/es.js` | Todas las frases que ve el usuario, incluidos los errores y los avisos con código |
| `js/texts/index.js` | Elige el idioma (`?idioma=va` o el último elegido) y rellena con castellano lo que falte |
| `js/errores.js` | De código estable de la API a frase que dice qué ha pasado y qué hacer |
| `js/categorias.js` | Agrupación de los tipos |
| `js/seguimiento.js` | Subida en fila, consultas periódicas con reintentos, avisos de página |
| `js/almacen.js` | Lo que recuerda el navegador: identificadores, nunca el nombre del fichero |

La pantalla envía solo lo que difiere del catálogo (`disabled_types` para los desmarcados que vienen
activos, `enabled_types` para los marcados que vienen apagados). **Para añadir el valenciano**:
crear `js/texts/va.js` con las mismas claves que `es.js` y registrarlo en `js/texts/index.js`.

`python scripts/probar_interfaz.py` recorre los flujos en un navegador real (Playwright) contra el
servicio arrancado y guarda capturas; las de las pantallas clave están en [`interfaz/`](interfaz/).

---

## API

Documentación interactiva en `/docs`.

| Método y ruta | Qué hace |
|---|---|
| `GET /entity-types` | Catálogo de tipos |
| `POST /jobs` | Encola un PDF, una imagen (JPEG, PNG, TIFF), un Word (.docx) o un Excel (.xlsx). **202** al terminar la subida, con `Location: /jobs/{id}` |
| `GET /jobs/{id}` | Estado, posición en cola, avance por páginas, avisos, resumen o error |
| `GET /jobs/{id}/result?output=pdf\|zip` | El PDF anonimizado (o PDF + `report.json` en ZIP) |
| `GET /jobs/{id}/report` | Informe completo en JSON |
| `POST /jobs/{id}/stop` | Parar y entregar lo procesado |
| `DELETE /jobs/{id}` | Eliminar sin entregar nada |
| `POST /anonymize` | Camino síncrono: procesa en la propia petición. PDF, PNG, JPG y TIFF |
| `GET /health` | Señal de vida (siempre 200) con GPU, modelos, cola, límites y problemas |
| `GET /health/ready` | Igual, pero **503** si falta spaCy o EasyOCR o si el trabajador de la cola no está vivo |

```bash
curl -i -X POST "http://localhost:8000/jobs" -F "file=@expediente.pdf"   # 202, Location: /jobs/3f2a…
curl http://localhost:8000/jobs/3f2a…                                    # hasta completed / failed
curl -o anonimizado.pdf "http://localhost:8000/jobs/3f2a…/result?output=pdf"
```

- **Estados**: `queued` → `running` → `completed` | `failed`; `expired` cuando se ha borrado.
- **Avisos en vivo** (`notices`): páginas tachadas enteras porque el OCR no pudo leerlas
  (`kind: page_redacted_in_full`, `reason: ocr_unreadable` u `ocr_timeout`), numeradas desde 1. Se
  cortan en 200 (`notices_total` dice cuántos hubo); el informe lleva la lista completa.
- **Avisos del informe con código** (`coded_warnings`, con `code`, `pages` y, si procede, `types` o
  `counts`): ver [Red de seguridad y verificación](#red-de-seguridad-y-verificación). Van también en
  el resumen de `GET /jobs/{id}`. `warnings` lleva la frase en inglés.
- **Errores al encolar**: 415 si el tipo declarado no es PDF ni imagen, 413 si supera
  `MAX_UPLOAD_MB` o `MAX_IMAGE_MEGAPIXELS`, 400 si por dentro no es un PDF, JPEG, PNG o TIFF válido, 422 si un tipo es inválido o está en las dos listas, 422 si supera `MAX_PDF_PAGES` o está
  cifrado o vacío, 503 con `Retry-After` si la cola está llena. **Al descargar**: 409 si no ha
  terminado o falló, 410 si caducó.
- **Parar y eliminar**: `DELETE` elimina sin entregar; `POST …/stop` para y entrega las páginas
  hechas. Procesándose, los dos responden **202** con `stopping: true` y se consuman al soltar la
  página en curso (digital: inmediato; escaneado: como mucho `OCR_PAGE_TIMEOUT_S`). `stop` sobre un
  trabajo en cola responde 409 `nothing_processed_yet`.
- **Entrega parcial**: solo páginas completas; las no procesadas se omiten, no se pintan de negro
  (pintar negro sobre una página digital no borra su texto). Lo dicen la última página (generada,
  sin nada del documento), el nombre de la descarga
  (`documento_anonimizado_parcial_1-34_de_118.pdf`) y el informe (`partial`, `pages`,
  `pages_total_original`). Pasa por la misma verificación que un documento entero.
- **Un documento a la vez** (`MAX_CONCURRENT_DOCUMENTS=1`): el límite de la GPU; `POST /anonymize`
  comparte el turno.
- **Informe en PDF** (`GET /jobs/{id}/report.pdf`, y `informe.pdf` dentro del zip):
  `app/services/informe_pdf.py`, con `fitz.Story` (HTML y CSS a PDF, sin dependencias nuevas). Se
  construye solo con recuentos, páginas y códigos (`entities_by_type`, `entities_by_page`,
  `pages_redacted_in_full`, `pages_redacted_by_content`, `coded_warnings`,
  `manual_review_reasons`, `metadata_removed`), nunca con `detected_entities`,
  `descartes_padron` ni las frases de `warnings`. Sin nombre de fichero: fecha, páginas e
  identificador. `tests/test_informe_pdf.py` comprueba que ningún valor aparece ni en el texto
  ni dentro del fichero, aunque el JSON los lleve.
- **`entities_by_page`**: datos por página (desde 1) y tipo; suma lo mismo que `entities_by_type`
  en los tres caminos (`tests/test_recuento_por_pagina.py`). **`pages_redacted_by_content`**:
  páginas tachadas enteras por ser copia de DNI (`dni_scan`) o autoliquidación (`tax_form`).
- **`treatment`**: `anonymize` (por defecto) o `metadata_only` (ver
  [Metadatos](#metadatos-y-contenido-oculto)); otro valor, 422. Va también en el informe y en el
  resumen del trabajo.
- **`disabled_types` y `enabled_types`**: un valor por parámetro (`?disabled_types=DATE_TIME&
  disabled_types=URL`). Se quitan espacios, se pasa a mayúsculas y se ignoran los vacíos; una lista
  vacía equivale a no enviarla. Sin ninguna de las dos se aplica el catálogo. Un tipo inexistente o
  en las dos listas: 422 sin procesar nada. No se admiten comas. La evaluación pide todos los tipos
  explícitamente.

---

## Evaluación

**Corpus** (proyecto hermano `../corpus-generator`): documentos sintéticos con verdad de referencia.

- Digital: 400 documentos (`output/pdfs`, `output/labels`).
- Escaneado: 28 (`output/scanned_mitad_pdfs`, `output/scanned_mitad_labels`).
- Híbrido: 24 (`output/hybrid_mitad_pdfs`, `output/hybrid_mitad_labels`).
- Extensión: 90 documentos difíciles (presupuestos de obra, copias de DNI, autoliquidaciones),
  limpios, escaneados y girados (`output/ext_pdfs`, `output/ext_labels`). Su única medición está en
  `evaluation/reference/results_ext.json`.

**Medir** (con la API arrancada; antes, `tests/test_casos_dificiles_reales.py`, los únicos que cubren
copias pegadas con texto encima):

```bash
python -m evaluation.run --input-pdfs ../corpus-generator/output/pdfs \
    --input-labels ../corpus-generator/output/labels --workers 2 \
    --checkpoint evaluation/digital.jsonl --resume \
    --results-json evaluation/resultados_digital.json --output evaluation/digital.html \
    --guardar-salidas evaluation/salidas_digital
```

`--guardar-salidas` guarda los PDF entregados, que necesita la lectura de barras. Los resultados en
la raíz de `evaluation/` están ignorados por git.

**Cómo se cuenta** (`evaluation/metrics.py`):

- por valor: cada dato distinto de un documento una vez; por aparición: cada vez que aparece;
- un dato que solo vive en páginas que salen en negro (≥ 90 % de píxeles oscuros) cuenta como
  detectado;
- una detección que es el mismo dato con el límite pasado («Luis Molina Medina NIF /») no es falso
  positivo: la barra de más cuenta como sobre-redacción;
- quedan fuera los tipos que no se anonimizan (`LOCATION`…) y los defectos conocidos del corpus,
  listados en un solo sitio: `evaluation/defectos_corpus.py`.

Dos efectos conocidos de la contabilidad: el lugar de nacimiento cuenta como falso positivo porque
el corpus no lo anota; y un teléfono partido en varias líneas se tacha entero, pero el informe lo
devuelve con saltos de línea («+34\n706\n206\n505») y el evaluador, que compara texto exacto, lo
cuenta a la vez como falso negativo y falso positivo.

**Corpus de imágenes** (`evaluation/corpus_imagenes.py` y `evaluation/lectura_imagenes.py`): 38
fotos y capturas inventadas con sus etiquetas (rectas, inclinadas, en perspectiva, giradas, con
mesa y sombras, de móvil y pequeñas, carnets oscuros, JPEG comprimidos, PNG con transparencia, TIFF
de dos páginas, copias de DNI de ejemplo). La transformación de cada foto se guarda y se deshace
para leer entrada y salida en plano; además de la lectura, se mide por píxeles si la barra cubre el
dato entero (asoma por debajo del 97 %) y si tapa los rótulos. Salidas en `evaluation/imagenes/`
(ignorada por git). Medición del 27-10: 171 datos tapados, 0 fugas, 72,5 % de rótulos a la vista,
0 imágenes tapadas enteras sin motivo.

**Corpus de Word** (`evaluation/corpus_word.py` y `evaluation/lectura_word.py`): 12 documentos
inventados (cartas, notificación en valenciano, formularios bilingües con tablas, contrato, acta,
declaración con copia de DNI incrustada, informe con comentarios y control de cambios, dos sin
datos personales) y uno «cargado» que esconde un dato en cada sitio donde un Word guarda texto (lo
usan los tests). Mide que el fichero se abre, que ningún dato queda en ninguna parte, que la imagen
incrustada sale tapada (OCR), que el texto legítimo se conserva y que un documento sin datos no
cambia. Medición del 27-10: 12/12 se abren, 53/53 datos eliminados, 72/72 textos legítimos
conservados, 2/2 documentos sin datos intactos.

**Corpus de Excel** (`evaluation/corpus_excel.py` y `evaluation/lectura_excel.py`): 10 libros
inventados (listado de solicitantes, inscritos en valenciano con título y cabecera en la fila 3,
columnas «Col1…» que solo delata el contenido, observaciones de texto libre, libro «cargado» con hoja
oculta, comentario, fórmulas, nombre definido y propiedades, libro con tabla dinámica, ayudas con sus
totales, estadística por distritos, y un presupuesto de obra dos veces: con las casillas del
catálogo, que no se toca, y con la de presupuesto marcada). Cada libro se procesa con las casillas
del catálogo más las que marque su etiqueta. Mide que se abre, que ningún dato queda, que cada columna personal lleva su marcador,
que las demás no cambian, que el texto legítimo se conserva y que los totales llevan el valor
esperado. Medición del 28-10: 10/10 se abren, 217 datos eliminados sin fugas, 31/31 columnas
personales y 19/19 no personales bien, 88/88 textos legítimos conservados, 6/6 totales bien.

**Lectura de barras** (`evaluation/lectura_salida.py`): extrae el texto del PDF entregado (y OCR
donde no hay texto) y busca cada dato etiquetado, tolerando erratas. Cada dato sale tapado, fuga, no
concluyente (no se encuentra ni en la entrada, o está bajo una página en negro) o fuera de alcance.

**Herramientas** (no llaman a la API):

| Herramienta | Uso |
|---|---|
| `python -m evaluation.agregado` | Las métricas de los tres corpus juntas (por defecto, las de c4) |
| `python -m evaluation.comparar_eval REF NUEVA` | Métricas globales y por tipo entre dos corridas |
| `python -m evaluation.comparar_documentos REF NUEVA` | Qué FN/FP aparecen o desaparecen, por documento |
| `python -m evaluation.guardar_ref` / `identidad_contra` | Guardar la salida antes de un refactor y comprobar que no cambia |
| `python -m evaluation.compuerta_tipos REF PRUEBA --desmarcados T1,T2` | Desmarcar un tipo no puede hacer desaparecer otro |
| `python -m evaluation.rebuild_report RESULTADOS.json INFORME.html` | Regenera el HTML de una corrida |
| `python -m evaluation.informe_final` | Regenera `evaluation/informe_final.html` desde `evaluation/reference/c4/` |
| `python -m evaluation.comprobar_metadatos SALIDA.pdf` | Comprobación de metadatos con ExifTool y qpdf |

**Referencias bajo seguimiento**:

- `evaluation/reference/c4/`: la medición final (resultados de los tres corpus, lectura de barras
  con las fugas clasificadas, banco de teléfonos y direcciones, comprobaciones).
- `evaluation/linea_base/`: la línea base, la tabla de todas las mediciones
  (`transicion_contabilidad.json`) y cómo leerla (`LEEME.md`). Sus resultados y
  `scripts/recontar_resultados.py` permiten volver a contarla si cambia el evaluador.

**Otras pruebas**:

- **Casos difíciles** (`scripts/casos_dificiles.py`, `tests/test_casos_dificiles.py`): certificado
  bancario con el IBAN repetido en la prosa, formulario bilingüe a dos columnas, anexo en A3
  apaisado, bloque de firmas en fila; en versión digital y escaneada. Los casos con un documento de
  identidad real viven fuera del repositorio (ver
  [Documentos reales de prueba](#documentos-reales-de-prueba)) y su test se salta sin ellos.
- **Banco de teléfonos y direcciones** (`scripts/banco_contacto.py --medir`): 107 formatos reales y
  23 trampas, en digital y escaneado.

---

## Despliegue

Pasos en el [README](../README.md#despliegue-con-docker). Decisiones:

- **CUDA 12.8 y torch 2.11.0**, fijados: la combinación medida. Necesita driver 570 o posterior; un
  driver nuevo ejecuta CUDA más antiguas, no al revés.
- **Base `python:3.12.6-slim-bookworm`**: el torch de cu128 trae CUDA y cuDNN; el driver lo inyecta
  nvidia-container-toolkit. `nvidia/cuda` duplicaría unos 3 GB.
- **Dependencias exactas** en `docker/requirements.txt` (el entorno medido, con `--no-deps`). Fuera:
  el paquete `mineru` (solo se usa `mineru-vl-utils`) y con él `opencv-python` (basta `headless`);
  lo del generador del corpus, los tests y lo de Windows. Sistema: `libzbar0`, `libglib2.0-0`,
  `libgomp1`.
- **Modelos dentro** (`/opt/modelos`, copiados de `data/model/`): MinerU 2,2 GB, EasyOCR 94 MB,
  spaCy. `HF_HUB_OFFLINE`,
  `TRANSFORMERS_OFFLINE` y `EASYOCR_ALLOW_DOWNLOAD=false`.
- **spaCy en CPU** (`NER_USE_GPU=false`): en GPU necesitaría CuPy; en CPU es como se midió.
- **Ficheros de trabajo en disco** (`/data/trabajos`, volumen): en memoria serían 5-8 GB de RAM con
  la cola llena. El disco de los volúmenes tiene que estar cifrado.
- **Usuario 10001**, sin root; solo escribe en `/data/trabajos`.
- **`HEALTHCHECK`** contra `/health/ready` y `restart: unless-stopped`.
- **Lista de permitidos** (`docker/Dockerfile.dockerignore`, que Docker lee por estar junto al
  Dockerfile): solo `app/`, las dependencias, los modelos y las licencias (`LICENSE` y
  `THIRD_PARTY_LICENSES.md`, que se copian a `/app`).
- **Nombre fijo del proyecto de compose** (`name: anonimizador`): el del volumen de trabajos no
  depende de la carpeta donde esté el fichero.
- **Ficheros**: todo en `docker/` (`Dockerfile`, su lista de permitidos, `docker-compose.yml`,
  `requirements.txt`). Se construye desde la raíz: `docker build -f docker/Dockerfile .`

Pruebas del contenedor (RTX 4060 de 8 GB, driver 572.61):

| Prueba | Resultado |
|---|---|
| Con GPU, un PDF de cada camino | Digital 6 s, escaneado 110 s, híbrido 34 s; tapa lo mismo que las salidas de c4 |
| Sin red (`--network none`) | Igual, sin intentar descargar nada |
| Sin GPU | Arranca y avisa; el digital igual; cada página escaneada agota los 360 s de OCR y sale tapada entera |
| Carpeta de trabajos sin permiso | No arranca; mensaje con el arreglo |
| Interfaz web desde el contenedor | Las 30 comprobaciones del guion de navegador |

Tamaños: fichero de transporte 6,6 GB; imagen cargada unos 11 GB.

---

## Documentos reales de prueba

Los documentos reales con datos personales que se usan para probar van **siempre** en
`data/samples/real/`, que está ignorada por git entera (con dos reglas más de red por si alguno se deja
fuera). Un documento que llega a un commit queda en el historial aunque se borre después.

La convención:

- nada derivado de esos documentos sale de `data/samples/real/`: ni salidas, ni imágenes, ni volcados;
- ningún test, comentario, registro ni commit lleva sus datos; un test que los usa lee el fichero
  por ruta y se salta si no está;
- ningún guion recorre `data/samples/real/`: el único recorrido de `data/samples/` es
  `scripts/muestras.py`,
  que la deja fuera, y lo comprueba un test.

Herramientas: `scripts/dividir_pdf.py` parte un PDF grande en trozos; `scripts/debug_ocr_image.py`
muestra qué lee el OCR en una imagen.
