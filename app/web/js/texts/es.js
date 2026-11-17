/**
 * Todas las frases que ve el usuario, en castellano.
 *
 * Están aquí y no repartidas por el código para poder traducirlas sin rehacer
 * la interfaz: para el valenciano basta con un `va.js` con las mismas claves.
 * Lo que falte en una traducción cae al castellano, nunca a un hueco vacío.
 *
 * Las frases de error son lo más delicado de este fichero: cada una dice QUÉ
 * ha pasado y QUÉ puede hacer la persona. `[soporte]` se sustituye por el
 * contacto real cuando el ayuntamiento nos lo dé.
 */

/** "la página 3", "las páginas 2 y 5", "las páginas 1, 4 y 7". */
function lasPaginas(paginas) {
  if (!paginas || !paginas.length) return "el documento";
  if (paginas.length === 1) return `la página ${paginas[0]}`;
  return `las páginas ${paginas.slice(0, -1).join(", ")} y ${paginas[paginas.length - 1]}`;
}

/** "algún dato (DNI o IBAN)": los tipos ya llegan con su nombre del catálogo. */
function losDatos(nombres) {
  if (!nombres || !nombres.length) return "algún dato";
  return `algún dato (${nombres.join(" o ")})`;
}

export const TEXTOS = {
  app: {
    titulo: "Anonimizador de documentos",
    organizacion: "Universitat de València",
  },

  salud: {
    activo: "Servicio activo",
    lento: "El servicio va más lento de lo normal",
    caido: "Servicio no disponible",
    sinComprobar: "Comprobando el servicio…",
    avisoLento:
      "Los documentos se anonimizan igual de bien, pero tardan bastante más, " +
      "sobre todo los escaneados. Si no es urgente, súbelos más tarde.",
    avisoCaido:
      "El servicio no puede anonimizar documentos ahora mismo. Inténtalo de " +
      "nuevo en unos minutos; si sigue así, avisa a [soporte].",
    avisoSinConexion:
      "No se puede conectar con el servicio. Comprueba que estás en la red de la " +
      "Universitat; si lo estás, puede estar parado: avisa a [soporte].",
  },

  paso1: {
    titulo: "Documentos",
    soltar:
      "Arrastra uno o varios PDF, imágenes (JPEG, PNG, TIFF), Word (.docx), Excel (.xlsx), " +
      "CSV o textos (.txt, .md) aquí, o selecciónalos",
    limites: (docs, paginas, mb) =>
      `Hasta ${docs} documentos por tanda · máximo ${paginas} páginas y ${mb} MB cada uno`,
    elegir: "Seleccionar archivos",
    elegidos: (n, peso) =>
      `${n} ${n === 1 ? "documento elegido" : "documentos elegidos"} · ${peso}`,
    quitar: "Quitar de la tanda",
    tandaGrande: (mb) =>
      `La tanda suma ${mb} MB. Puede tardar en subirse; si no es urgente, ` +
      `divídela en varias.`,
  },

  paso2: {
    titulo: "Qué se va a ocultar",
    ayuda: "Todo está marcado por defecto. Despliega un grupo para elegir tipos sueltos.",
    activos: (activos, total) => `${activos} de ${total} tipos activos`,
    seAplica: (n) =>
      n === 1
        ? "Se aplicará al documento de la tanda."
        : `Se aplicará a los ${n} documentos de la tanda.`,
    quedanVisibles: (nombres) => `Quedarán visibles: ${nombres}.`,
    avisoQr:
      "Los códigos que lleven dentro otros datos que sigues ocultando se " +
      "taparán igualmente. Por ejemplo, el QR de la sede electrónica contiene " +
      "una dirección web y un código seguro de verificación: para conservarlo, " +
      "desmarca también esos dos tipos.",
    tratamiento: "Qué hacer con los documentos",
    tratAnonimizar: "Anonimizar: ocultar los datos personales",
    tratMetadatos: "Solo quitar metadatos",
    tratSeudonimizar: "Seudonimizar: cambiar los datos por etiquetas que se pueden deshacer",
    ayudaSeudonimizar:
      "Solo Word, Markdown y texto. Sirve para pasar un documento por un modelo de lenguaje sin " +
      "que vea los datos personales: cada dato pasa a una etiqueta como [PERSONA_1], y aparte " +
      "recibes una tabla cifrada para devolver los datos a su sitio en «Recuperar datos».",
    avisoSeudonimizar:
      "Seudonimizar NO es anonimizar. Con la tabla se recuperan los datos, así que el documento " +
      "con etiquetas sigue conteniendo datos personales: trata la tabla y su contraseña como " +
      "los propios datos. Lo que no se detecte seguirá a la vista: revisa el documento antes de " +
      "enviarlo a ningún sitio.",
    avisoSinHttps:
      "Esta página no usa HTTPS: la contraseña viajará sin cifrar por la red. Avisa a quien " +
      "administra el servicio antes de seudonimizar documentos reales.",
    clave: "Contraseña para la tabla (12 caracteres o más)",
    clave2: "Repite la contraseña",
    claveNota:
      "No se guarda en ningún sitio: si la pierdes, no podrás recuperar los datos.",
    // Se dicen con texto (y un signo), no solo con el color, y los lee el lector de pantalla.
    claveFaltan: (n) => (n === 1 ? "Falta 1 carácter" : `Faltan ${n} caracteres`),
    claveLista: "✓ Longitud suficiente",
    claveCoincide: "✓ Coinciden",
    claveDistinta: "✗ No coincide con la contraseña",
    noSeudonimizable:
      "No se puede seudonimizar (solo Word, Markdown y texto). Anonimízalo o quita solo sus metadatos.",
    avisoMetadatos:
      "El contenido del documento NO se revisa ni se anonimiza: los datos " +
      "personales que se vean en él siguen ahí. Solo se quitan los metadatos, " +
      "los comentarios y el texto oculto, y los campos de formulario dejan de " +
      "ser rellenables. Los textos (.txt, .md) y los CSV no tienen metadatos: salen igual.",
    nuncaSeOculta: "¿Qué no se oculta nunca?",
    nuncaSeOcultaDetalle: [
      "Nombres, teléfonos y direcciones de organismos públicos.",
      "Números de colegiado profesional.",
      "Firmas manuscritas y sellos.",
    ],
  },

  paso3: {
    titulo: "Seguimiento",
    vacio: "Añade uno o varios documentos para empezar.",
    sinEmpezar: "Pulsa «Anonimizar» y aquí verás el avance de cada documento.",
    anonimizar: "Anonimizar",
    quitarMetadatos: "Quitar metadatos",
    seudonimizar: "Seudonimizar",
    resumen: (partes) => partes.join(" · "),
    resumenTerminados: (n) => `${n} ${n === 1 ? "terminado" : "terminados"}`,
    resumenParciales: (n) => `${n} ${n === 1 ? "parcial" : "parciales"}`,
    resumenProcesando: (n) => `${n} ${n === 1 ? "procesándose" : "procesándose"}`,
    resumenEnCola: (n) => `${n} en cola`,
    resumenConProblemas: (n) => `${n} con ${n === 1 ? "problema" : "problemas"}`,
    enCurso: (indice, total, pagina, paginas) =>
      `Procesando documento ${indice} de ${total} · página ${pagina} de ${paginas}`,
    enCursoSinPaginas: (indice, total) =>
      `Procesando documento ${indice} de ${total} · abriendo el documento…`,
    colaCargada:
      "Los escaneados pueden tardar horas: puedes cerrar esta página y volver " +
      "más tarde, el trabajo sigue en el servidor.",
    // Se compone con lo que dice el servidor (plazo y si guarda el original):
    // el aviso no puede prometer un borrado que no ocurre.
    retencion: ({ plazo, guardaOriginal }) =>
      guardaOriginal
        ? `El documento original se guarda en el servidor junto al anonimizado. ` +
          `Los dos se borran ${plazo} después de terminar: descárgalo antes.`
        : `El documento original se borra del servidor en cuanto termina de ` +
          `procesarse. El anonimizado se borra ${plazo} después: descárgalo antes.`,
    plazo: (minutos) =>
      minutos % 60 === 0
        ? `${minutos / 60} ${minutos === 60 ? "hora" : "horas"}`
        : `${minutos} minutos`,
    paginaLenta: (pagina) =>
      `La página ${pagina} está tardando más de lo normal. Es habitual en ` +
      `escaneados y planos; el sistema sigue trabajando.`,
  },

  documento: {
    subiendo: (porcentaje) => `Subiendo… ${porcentaje} %`,
    esperandoSubida: "Esperando para subir",
    enCola: (delante) =>
      delante === 0 ? "En cola · es el siguiente" : `En cola · ${delante} por delante`,
    procesando: (pagina, paginas) =>
      paginas ? `Procesando · página ${pagina} de ${paginas}` : "Procesando · abriendo el documento…",
    parando: "Parando… termina la página en curso y entrega lo procesado",
    terminado: (datos, paginas) =>
      `Terminado · ${datos} ${datos === 1 ? "dato ocultado" : "datos ocultados"} ` +
      `en ${paginas} ${paginas === 1 ? "página" : "páginas"}`,
    seudonimizado: (etiquetas, datos) =>
      `Terminado · ${datos} ${datos === 1 ? "dato cambiado" : "datos cambiados"} por ` +
      `${etiquetas} ${etiquetas === 1 ? "etiqueta" : "etiquetas"} · descarga el documento y la tabla`,
    sinMetadatos:
      "Terminado · sale igual: este formato no tiene metadatos · el contenido NO se ha " +
      "revisado ni anonimizado",
    soloMetadatos: (paginas) =>
      `Terminado · metadatos quitados en ${paginas} ${paginas === 1 ? "página" : "páginas"} · ` +
      `el contenido NO se ha revisado ni anonimizado`,
    parcial: (hasta, total) =>
      `Parcial · páginas 1 a ${hasta} de ${total} · el resto no se ha anonimizado`,
    revisar: "Revísalo antes de usarlo: hay partes que no se han podido leer con seguridad",
    // Por que se ha tapado entera una pagina (o la imagen), en lenguaje llano.
    tachadaImagen: (motivo) => `La imagen se ha tapado entera porque ${motivo}.`,
    tachadaPagina: (n, motivo) => `Página ${n} tapada entera porque ${motivo}.`,
    motivosTachada: {
      dni_scan: "es una copia de un documento de identidad",
      tax_form: "es una autoliquidación de impuestos",
      ocr_unreadable:
        "no se ha podido leer, ni girándola, enderezándola o invirtiendo los colores",
      ocr_timeout: "su lectura tardó demasiado y se ha tapado por seguridad",
    },
    paginasTachadas: (n) =>
      `${n} ${n === 1 ? "página tachada entera" : "páginas tachadas enteras"} porque no se ${
        n === 1 ? "pudo" : "pudieron"
      } leer`,
  },

  acciones: {
    descargar: "Descargar",
    descargarParcial: "Descargar parcial",
    documentoConEtiquetas: "Documento con etiquetas",
    tabla: "Tabla para recuperar los datos (cifrada)",
    informePdf: "Informe en PDF",
    informe: "Informe técnico (JSON)",
    parar: "Parar",
    eliminar: "Eliminar",
    quitar: "Quitar de la lista",
    borrarDelServidor: "Borrar ya del servidor",
    reintentar: "Reintentar",
    subirDeNuevo: "Subir de nuevo",
    recargar: "Recargar la página",
    cancelar: "Cancelar",
  },

  pestanas: {
    anonimizar: "Anonimizar documentos",
    recuperar: "Recuperar datos",
  },

  recuperar: {
    paso1: "Texto con etiquetas",
    ayuda1:
      "Pega el texto que lleva las etiquetas (por ejemplo, el resumen que ha hecho el modelo " +
      "de lenguaje) o elige un archivo de texto, Markdown o Word.",
    pegar: "Pega aquí el texto con etiquetas como [PERSONA_1]…",
    elegirFichero: "O elegir un archivo (.txt, .md, .docx)",
    ficheroElegido: (nombre) => `Archivo: ${nombre}`,
    paso2: "Tabla",
    ayuda2: "El archivo .tabla que descargaste al seudonimizar el documento.",
    elegirTabla: "Elegir la tabla (.tabla)",
    quitarTabla: "Quitar la tabla",
    quitarFichero: "Quitar el archivo",
    tablaElegida: (nombre) => `Tabla: ${nombre}`,
    sinTabla: "Ninguna tabla elegida",
    paso3: "Contraseña",
    contrasena: "La contraseña que pusiste al seudonimizar",
    lanzar: "Recuperar los datos",
    resultado: "Resultado",
    exacta: (n) =>
      `Documento recuperado tal como era: ${n} ${n === 1 ? "dato devuelto" : "datos devueltos"} a su sitio.`,
    tolerante: (n) =>
      `${n} ${n === 1 ? "etiqueta cambiada" : "etiquetas cambiadas"} por su dato.`,
    noResueltas:
      "Etiquetas que no están en la tabla: se han dejado como estaban. Resuélvelas a mano.",
    alteradas: "Etiquetas que venían escritas de otra forma: comprueba que el dato es el que toca.",
    alterada: (forma, etiqueta) => `${forma} → ${etiqueta}`,
    noUsadas: "Datos de la tabla que no aparecen en el texto (solo para que lo sepas):",
    sinVista: "Es un documento de Word: descárgalo para verlo.",
    copiar: "Copiar el texto",
    copiado: "Copiado",
    descargar: "Descargar",
  },

  confirmar: {
    eliminarTitulo: "¿Eliminar este documento?",
    eliminarTexto:
      "Se detiene el proceso y se borra todo lo relacionado con él del " +
      "servidor. No se podrá recuperar ni descargar.",
  },

  /**
   * Avisos del informe con código estable (`coded_warnings`).  La clave es el
   * código; lo que esta versión de la página no conozca no se muestra (sigue
   * en el informe técnico, en inglés).
   */
  avisosInforme: {
    plan_titleblock_only_verified: ({ paginas }) =>
      `En ${lasPaginas(paginas)}, que es un plano, solo se ha comprobado el cajetín: ` +
      `es lo único que se anonimiza en un plano.`,
    pages_not_verified: ({ paginas }) =>
      `La comprobación final no ha podido revisar ${lasPaginas(paginas)}. ` +
      `Revísalo antes de usarlo.`,
    safety_net_acted: ({ paginas }) =>
      `La comprobación final aún leía datos en ${lasPaginas(paginas)} y se han tapado. ` +
      `Conviene echarle un vistazo.`,
    content_not_reviewed: () =>
      "Solo se han quitado los metadatos: los datos personales que se ven en el " +
      "documento siguen ahí.",
    // Va en el informe; en la pantalla no se repite: ya lo dice el aviso al elegir seudonimizar.
    pseudonymized_not_anonymous: () => null,
    pseudonym_unrecoverable: ({ tipos, cuantos }) =>
      `${cuantos} ${cuantos === 1 ? "dato lleva" : "datos llevan"} el marcador de su tipo y no una ` +
      `etiqueta (${tipos.join(", ").toLowerCase() || "sin tipo"}): estaban en controles, formas o ` +
      `imágenes del documento y no se podrán recuperar con la tabla.`,
    password_without_https: () =>
      "La contraseña ha viajado sin cifrar (el servicio no usa HTTPS). Avisa a quien lo administra.",
    format_without_metadata: () =>
      "Los archivos de texto, Markdown y CSV no tienen metadatos: el archivo sale igual que entró.",
    text_encoding_uncertain: () =>
      "No estaba claro cómo estaba guardado el texto (su codificación). Comprueba que " +
      "las tildes y las eñes se leen bien.",
    digital_signature_invalidated: () =>
      "El documento llevaba firma digital y, al modificarlo, la firma deja de ser " +
      "válida. Si necesitas la firma, usa el original.",
    safety_net_still_readable: ({ paginas, tipos }) =>
      `Puede que siga legible ${losDatos(tipos)} en ${lasPaginas(paginas)}. ` +
      `Revísalo antes de usarlo.`,
  },

  avisos: {
    paginaTachadaTitulo: "Página tachada entera",
    paginaTachada: (pagina, motivo) =>
      `La página ${pagina} ${
        motivo === "ocr_timeout"
          ? "ha tardado demasiado en leerse"
          : "no se ha podido leer"
      } y se ha tapado por completo. Revísala al descargar.`,
    cerrar: "Cerrar aviso",
  },

  /**
   * Errores. La clave es el código estable que da la API; `_generico` cubre
   * cualquier código que esta versión de la página no conozca todavía.
   */
  errores: {
    noEsPdf: {
      titulo: "Este archivo no se puede anonimizar",
      texto:
        "Se pueden anonimizar documentos PDF, imágenes JPEG, PNG o TIFF, " +
        "documentos de Word (.docx), hojas de Excel (.xlsx), CSV y textos (.txt, .md).",
    },
    legacy_office: {
      titulo: "Este documento es del formato antiguo de Office",
      texto:
        "Los .doc y .xls no se pueden anonimizar. Ábrelo en Word o Excel y guárdalo " +
        "como .docx o .xlsx (Archivo › Guardar como), y vuelve a subirlo.",
    },
    office_too_large: {
      titulo: "El documento es demasiado grande para revisarlo de una vez",
      texto:
        "Tiene más celdas o párrafos de los que el servicio puede revisar de una vez. " +
        "Divídelo en varios archivos (por hojas o por partes) y súbelos por separado.",
    },
    processing_timeout: {
      titulo: "El documento ha tardado demasiado",
      texto:
        "Se ha parado para no bloquear el servicio y no se ha entregado nada. " +
        "Divídelo en partes más pequeñas y súbelas por separado.",
    },
    office_macros: {
      titulo: "Este documento tiene macros",
      texto:
        "No se anonimizan documentos con macros (.docm, .xlsm). Guárdalo como " +
        ".docx o .xlsx sin macros (Archivo › Guardar como) y vuelve a subirlo.",
    },
    invalid_file: {
      titulo: "Este archivo está dañado o no es lo que parece",
      texto:
        "Por dentro no es un documento válido de los que se admiten, o está dañado. Ábrelo en " +
        "tu ordenador: si se ve bien, guarda una copia nueva (o imprímela como " +
        "PDF) y sube esa copia.",
    },
    encrypted_pdf: {
      titulo: "Este PDF está protegido con contraseña",
      texto:
        "El sistema no puede leer documentos con contraseña. Ábrelo con la " +
        "contraseña y guárdalo sin ella, o pide una copia sin protección a quien " +
        "te lo envió.",
    },
    empty_pdf: {
      titulo: "Este PDF está vacío",
      texto:
        "No tiene páginas o están todas en blanco. Comprueba que has elegido el " +
        "archivo correcto.",
    },
    too_many_pages: {
      titulo: "El documento tiene demasiadas páginas",
      texto: (d) =>
        `El máximo son ${d.limite} páginas. Divídelo en partes y súbelas por separado.`,
    },
    document_too_large: {
      titulo: "El archivo es demasiado grande",
      texto: (d) =>
        `Pesa ${d.tamano} MB y el máximo son ${d.limite} MB. Divídelo en partes y ` +
        `súbelas por separado.`,
    },
    cola_llena: {
      titulo: "Hay demasiados documentos esperando",
      texto:
        "El servicio no admite más documentos ahora mismo. Vuelve a intentarlo " +
        "en unos minutos; no hace falta elegir el archivo otra vez.",
    },
    subida_interrumpida: {
      titulo: "La subida se ha interrumpido",
      texto:
        "El documento no llegó completo y no se ha procesado. Comprueba tu " +
        "conexión y vuelve a intentarlo.",
    },
    sin_conexion: {
      titulo: "Se ha perdido la conexión",
      texto:
        "Tu documento se sigue procesando en el servidor. Estamos reintentando; " +
        "en cuanto vuelva la conexión verás el avance. Puedes cerrar esta página " +
        "y volver más tarde.",
    },
    servicio_reiniciado: {
      titulo: "Se ha perdido el documento en proceso",
      texto:
        "El servicio se ha reiniciado y el documento no llegó a terminar. No se " +
        "ha guardado nada: vuelve a subir el original.",
    },
    ya_no_existe: {
      titulo: "Este documento ya no está en el servidor",
      texto:
        "Se borró por seguridad o lo eliminó alguien. Si aún lo necesitas, vuelve " +
        "a subir el original.",
    },
    parado_sin_paginas: {
      titulo: "No dio tiempo a procesar ninguna página",
      texto:
        "Se ha parado antes de terminar la primera página, así que no hay nada " +
        "que descargar. El documento se ha borrado del servidor.",
    },
    nothing_processed_yet: {
      titulo: "Todavía no ha empezado",
      texto:
        "El documento aún espera turno, así que no hay nada procesado que " +
        "entregar. Si no lo quieres, elimínalo.",
    },
    already_finished: {
      titulo: "El documento ya había terminado",
      texto: "Puedes descargarlo tal cual.",
    },
    pseudonymize_unsupported: {
      titulo: "Este formato no se puede seudonimizar",
      texto:
        "Solo se seudonimizan documentos de Word (.docx), Markdown (.md) y texto (.txt). " +
        "Anonimízalo o quita solo sus metadatos.",
    },
    password_too_short: {
      titulo: "La contraseña es demasiado corta",
      texto: "Tiene que tener 12 caracteres o más.",
    },
    table_unreadable: {
      titulo: "La tabla no se puede abrir",
      texto:
        "La contraseña no es la de esta tabla, o el archivo está dañado. Comprueba la " +
        "contraseña y que el archivo es el .tabla que descargaste.",
    },
    pseudonymization_disabled: {
      titulo: "La seudonimización está desactivada en este servicio",
      texto: "Pide a quien lo administra que la active.",
    },
    nothing_to_reconstruct: {
      titulo: "Falta el texto",
      texto: "Pega el texto con etiquetas o elige un archivo.",
    },
    pagina_antigua: {
      titulo: "La página se ha actualizado",
      texto:
        "La lista de datos a ocultar ha cambiado desde que abriste la página. " +
        "Recárgala y vuelve a intentarlo.",
    },
    _generico: {
      titulo: "No se ha podido anonimizar este documento",
      texto: (d) =>
        "Ha fallado el servicio mientras lo procesaba. Vuelve a subirlo una vez; " +
        `si falla de nuevo, avisa a [soporte] e indica esta referencia: ${d.referencia}.`,
    },
  },
};
