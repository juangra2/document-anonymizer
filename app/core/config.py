from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "document-anonymizer"
    environment: str = "local"

    temp_root: str = "/tmp/anonymizer"

    # Limites de entrada.  Los anteriores (25 MB / 50 paginas) venian de la
    # primera fase y habrian rechazado los expedientes reales del ayuntamiento,
    # que son cientos de paginas concatenadas.
    #
    # Medido en este equipo (portatil con RTX 4060, un solo documento a la vez):
    #
    #   camino digital     600 paginas en 8 min 34 s (0.86 s/pag), pico 1.2 GB
    #                      301 paginas en 4 min 55 s (0.98 s/pag), pico 1.1 GB
    #   camino escaneado    10 paginas en 17 min (101.8 s/pag),    pico 2.5 GB
    #
    # El tiempo escala lineal y la memoria es plana: el documento no se sostiene
    # entero en RAM, asi que el techo no lo pone la maquina sino la paciencia.
    # 300 paginas son ~5 min si el PDF es digital y varias horas si esta
    # escaneado entero; el presupuesto por pagina (ocr_page_timeout_s) y la cola
    # son lo que evita que un documento asi bloquee el servicio para siempre.
    max_upload_mb: int = 200
    max_pdf_pages: int = 300
    min_digital_text_chars: int = 30

    pdf_processing_mode: Literal["auto", "raster_ocr", "hybrid_secure"] = "hybrid_secure"

    store_original: bool = False
    store_redacted_temp: bool = True
    delete_temp_files: bool = True
    log_pii: bool = False

    # --- Trabajos asíncronos ------------------------------------------------
    # El documento se procesa en una cola en proceso y el cliente consulta el
    # estado por su identificador.  La cola NO sobrevive a un reinicio: los
    # trabajos en curso se pierden y hay que volver a subir el documento.  Es
    # una decisión consciente — un broker externo (Redis, Celery) añadiría un
    # servicio más que operar para un solo nodo con una sola GPU.
    #
    # Documentos procesados a la vez.  Es también el número de trabajadores y
    # el límite de acceso a la GPU: con más de uno, dos peticiones simultáneas
    # de OCR agotan la memoria de la tarjeta (medido: 500 por CUDA OOM).
    max_concurrent_documents: int = 1
    # Trabajos admitidos en cola antes de rechazar con 503.  Evita que una
    # tanda de subidas acumule gigas de PDF en disco esperando turno.
    job_queue_max: int = 20
    # Minutos que se conserva el resultado antes de borrarlo del disco.  Seis
    # horas: el documento anonimizado ya no lleva datos personales, y con una
    # sola hora una tanda larga caducaba por delante mientras aún se procesaba
    # el final.  El informe que se conserva con él tampoco lleva valores (la
    # cola no admite include_entities), y el original se borra en cuanto
    # termina el proceso, salvo store_original.
    result_ttl_minutes: int = 360
    # Cada cuánto barre el limpiador.  Un trabajo caduca aunque nadie lo
    # consulte: el borrado no puede depender de que el cliente vuelva.
    job_sweep_interval_s: int = 60

    # Prefijo de ruta cuando la aplicación se sirve tras un proxy inverso
    # (p. ej. "/anonimizador").  Vacío si cuelga de la raíz del dominio.
    root_path: str = ""

    presidio_score_threshold: float = 0.55
    default_redaction_mode: Literal["strict"] = "strict"

    # NER backend for PERSON/LOCATION/ORGANIZATION detection.
    # "spacy"        → es_core_news_md (default; fast, no download).
    # "transformers" → a HuggingFace token-classification model wrapped by
    #                  Presidio's TransformersNlpEngine.  Falls back to spaCy if
    #                  the transformer stack is unavailable.  Model loads lazily.
    # Default is "spacy": BETO (mrm8488) was evaluated and regressed PERSON F1
    # (0.80 vs 0.82) and mishandles Valencian/Catalan (span-alignment failures on
    # D'UNA PART:, D.N.I.:).  Engine kept configurable for future opt-in.
    # INE public gazetteers (surnames/given names + municipalities/provinces).
    # gazetteer_address: compositional ADDRESS extractor with CP↔province↔
    #   municipality validation.  gazetteer_person: reinforce low-confidence
    #   PERSON NER on name/surname matches and drop toponym false positives.
    gazetteer_address: bool = True
    gazetteer_person: bool = True

    # Redact the administrative case-file number (e.g. E-03001-2022-000074-00).
    # Off by default: the expediente is a public administrative reference.  The
    # output-register / "número de salida" is always redacted (it identifies the
    # specific communication addressed to a citizen).
    redact_case_file_number: bool = False

    # Construction-plan pages (A1/A0 drawings).  Detected from fitz metadata
    # BEFORE rasterising or running any OCR (O(ms)), so a 30+ MP plan never
    # stalls the pipeline.  PII on a plan lives in the title block (cajetín);
    # the drawing body is never processed.
    plan_page_enabled: bool = True
    # ≥A2 gate: short side and long side minimums in PDF points (A2 = 1191×1684).
    plan_min_short_side_pts: float = 1150.0
    plan_min_long_side_pts: float = 1600.0
    # Graphic dominance: massive vector content OR a dominant embedded image
    # (the image branch reuses ocr_image_dominant_*).  A text A4 has <200 paths.
    plan_min_drawings: int = 2000
    # "No paragraphs": at most this many text blocks of ≥150 chars.
    plan_max_paragraph_blocks: int = 5

    # Redaccion total de pagina (bloque B): copia de DNI y autoliquidacion
    # tributaria.  Se decide por SENALES TEXTUALES sobre el texto ya extraido
    # (vale igual para PDF digital y para OCR), nunca por analisis visual: un
    # DNI escaneado y pegado en un A4 no conserva proporciones de tarjeta.
    #
    # El modo de fallo es caro — un falso positivo pinta una pagina entera —, de
    # ahi que los umbrales se expongan: ante FP sobre documentacion normal se
    # sube el umbral y se pierde recall antes que arriesgar una pagina en negro.
    full_redaction_enabled: bool = True
    # Senales distintas necesarias para clasificar la pagina.
    dni_scan_min_signals: int = 2
    tax_form_min_signals: int = 2

    # Recorte de la tarjeta en copias de DNI.  Un DNI escaneado en plano ocupa
    # una fraccion pequena de un A4 casi vacio, asi que pintar la pagina entera
    # es correcto pero visualmente drastico.  El recorte NO se aplica por
    # confianza: se pinta, se vuelve a pasar OCR y se exige que no quede texto
    # legible fuera; si queda, se redacta la pagina completa.
    dni_crop_enabled: bool = True
    # Margen alrededor de la region detectada.  Generoso a proposito: la tarjeta
    # viene inclinada respecto al eje y el contorno no es exacto.
    dni_crop_margin_mm: float = 8.0
    # Fraccion de pagina admisible para la region recortada.  Por encima del
    # maximo no hay nada que ganar recortando.
    dni_crop_min_frac: float = 0.02
    dni_crop_max_frac: float = 0.60
    # Relleno minimo (area de tinta / area del rectangulo) para tomar una mancha
    # por contenido real.  Medido: 0.007 en el moteado de ruido que abarca la
    # hoja entera, frente a 0.21-0.52 en la tarjeta.
    dni_crop_min_fill: float = 0.05

    # Detect and redact handwritten signatures (embedded images / ink) in the
    # digital PDF path, anchored to a signature cue ("Fdo.", "Firma", …).
    # Off by default: the current heuristics over-redact (legitimate text/logos);
    # re-enable per deployment once thresholds are calibrated.
    detect_visual_signatures: bool = False

    # Detect and redact stamp/seal graphics (circular/elliptical marks, e.g.
    # notarial seals carrying an ID number) in the scanned/OCR path.
    # Off by default for the same reason (circular logos were over-redacted).
    detect_visual_stamps: bool = False

    ner_engine: Literal["transformers", "spacy"] = "spacy"
    ner_transformers_model: str = "mrm8488/bert-spanish-cased-finetuned-ner"
    ner_spacy_model: str = "es_core_news_md"
    ner_use_gpu: bool = True

    model_config = SettingsConfigDict(env_file=".env")

    # "auto" tries MinerU first and falls back to easyocr.
    # Explicit values: "mineru", "easyocr".
    ocr_engine: str = "auto"
    ocr_use_gpu: bool = True

    # Engine used when the primary (ocr_engine) is unavailable.
    ocr_fallback_engine: str = "easyocr"

    # MinerU backend passed to MinerUClient(backend=...).
    # "transformers" for MinerU2.5-Pro via mineru-vl-utils.
    ocr_mineru_backend: str = "transformers"

    # Local path to the downloaded MinerU2.5-Pro model weights.
    # Download: hf download opendatalab/MinerU2.5-Pro-2604-1.2B \
    #               --local-dir model/MinerU2.5-Pro-2604-1.2B
    ocr_mineru_model_path: str = "model/MinerU2.5-Pro-2604-1.2B"

    # Tipos de bloque cuyo CONTENIDO no se pide al VLM de MinerU.  Un bloque
    # "image" no aporta texto util —el texto ya lo da EasyOCR— y en cambio
    # dispara una transcripcion larguisima: medido sobre un DNI escaneado, la
    # deteccion de layout tarda 3.9 s y devuelve UN bloque de tipo image, cuya
    # extraccion se llevaba ~27 minutos para acabar descartada por degenerada.
    ocr_mineru_skip_block_types: str = "image,image_block,chart"

    # Cap on tokens the MinerU VLM may generate per block.  Bounds the runaway
    # generation seen on noisy regions (e.g. a dotted line decoded as an endless
    # "α₁ 与 α₂ 与 …" formula that took >11 min on one page).  Generous enough
    # for a dense legal paragraph; 0 disables the cap.
    ocr_mineru_max_new_tokens: int = 2048

    # Correccion automatica de orientacion en paginas escaneadas.  Un escaneo
    # con el contenido girado 90 grados hace que el OCR devuelva ruido, no
    # vacio, y sin esto no se detecta ni se avisa de nada (ver
    # services/ocr/orientation.py).
    ocr_auto_rotate_enabled: bool = True
    # Caracteres por token por debajo de los cuales el texto se considera
    # ruido.  Medido sobre el corpus: paginas normales 14.2-35.1, pagina
    # girada 2.1-2.8, la misma pagina corregida 10.5-12.0.
    ocr_min_chars_per_token: float = 8.0
    # Tokens minimos para molestarse en probar otras orientaciones: por debajo
    # la pagina esta practicamente en blanco y rotarla no aporta.
    ocr_rotate_min_tokens: int = 8

    # Carpeta con los pesos de EasyOCR (detector craft_mlt_25k.pth y
    # reconocedor latin_g2.pth).  Se declara para poder llevarlos dentro de la
    # imagen: en el servidor del ayuntamiento puede no haber salida a internet,
    # y EasyOCR los descarga en el primer uso si no los encuentra.  Vacio =
    # ubicacion por defecto (~/.EasyOCR/model).
    easyocr_model_path: str = ""
    # Permitir que EasyOCR descargue lo que le falte.  En el despliegue final
    # va a false: mejor fallar al arrancar que quedarse colgado pidiendo red.
    easyocr_allow_download: bool = True

    ocr_pdf_render_dpi: int = 200
    ocr_second_pass_verification: bool = True

    # Per-page OCR budget (seconds).  A page that exceeds it (e.g. a very large
    # blueprint that stalls the OCR engine) does not hang the whole job: it is
    # handled as an unreadable page (see redact_unreadable_pages).
    ocr_page_timeout_s: int = 360

    # Pagina que el OCR no ha podido leer —ilegible en todos los giros o que
    # agota ocr_page_timeout_s—: se tacha ENTERA.  Entregarla intacta con un
    # aviso dejaba visible todo su contenido; medido en un presupuesto
    # escaneado girado: nombre del tecnico, empresa y fecha legibles.  Si no se
    # ha podido leer, no se puede afirmar que no lleve datos.  False vuelve al
    # comportamiento anterior (pagina intacta + aviso de revision manual).
    redact_unreadable_pages: bool = True

    # "Image-dominant" page fast path: a page whose largest embedded image covers
    # at least this fraction of the page AND exceeds this pixel count is treated
    # as a plan/photo — heavy text OCR is skipped (only visual codes/signatures
    # are redacted), avoiding multi-minute stalls.  The megapixel floor is set
    # well above a normal A4 scan (~9 MP at 300 DPI) so ordinary scanned text
    # pages still get full OCR.  The megapixel floor (25 MP) clears a 600-DPI A4
    # scan, so only oversized plans (A1/A0, tens of MP) take the fast path; the
    # per-page timeout is the safety net for everything in between.
    ocr_image_dominant_area_frac: float = 0.70
    ocr_image_dominant_megapixels: float = 25.0

    # Post-redaction verification thresholds [0.0–1.0].
    # review  : risk >= threshold → manual_review_required = True.
    # reject  : risk >= threshold → RedactionVerificationError raised.
    verify_risk_threshold_review: float = 0.30
    verify_risk_threshold_reject: float = 0.70
    # Comma-separated layer names active for post-redaction verification.
    verify_layers_strict:   str = "textual,metadata,binary"
    # OCR verification layer is opt-in — it doubles processing time.
    verify_ocr_layer_enabled: bool = False


settings = Settings()