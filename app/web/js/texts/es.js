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
export const TEXTOS = {
  app: {
    titulo: "Anonimizador de documentos",
    organizacion: "Ajuntament de València",
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
      "No se puede conectar con el servicio. Comprueba que estás en la red del " +
      "ayuntamiento; si lo estás, puede estar parado: avisa a [soporte].",
  },

  paso1: {
    titulo: "Documentos",
    soltar: "Arrastra uno o varios PDF aquí, o selecciónalos",
    limites: (docs, paginas, mb) =>
      `Hasta ${docs} documentos por tanda · máximo ${paginas} páginas y ${mb} MB cada uno`,
    elegir: "Seleccionar archivos",
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
    anonimizar: "Anonimizar",
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
    retencion:
      "Los documentos anonimizados se borran del servidor 6 horas después de " +
      "terminar. Descárgalos antes de ese plazo.",
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
    parcial: (hasta, total) =>
      `Parcial · páginas 1 a ${hasta} de ${total} · el resto no se ha anonimizado`,
    revisar: "Revísalo antes de usarlo: hay partes que no se han podido leer con seguridad",
    paginasTachadas: (n) =>
      `${n} ${n === 1 ? "página tachada entera" : "páginas tachadas enteras"} porque no se ${
        n === 1 ? "pudo" : "pudieron"
      } leer`,
    caducado: (hora) =>
      `Este documento se borró del servidor a las ${hora}, seis horas después de ` +
      `terminar. Si aún lo necesitas, vuelve a subir el original.`,
    disponibleHasta: (hora) => `Disponible hasta las ${hora}`,
  },

  acciones: {
    descargar: "Descargar",
    descargarParcial: "Descargar parcial",
    informe: "Informe técnico",
    parar: "Parar",
    eliminar: "Eliminar",
    quitar: "Quitar de la lista",
    borrarDelServidor: "Borrar ya del servidor",
    reintentar: "Reintentar",
    subirDeNuevo: "Subir de nuevo",
    recargar: "Recargar la página",
    cancelar: "Cancelar",
  },

  confirmar: {
    eliminarTitulo: "¿Eliminar este documento?",
    eliminarTexto:
      "Se detiene el proceso y se borra todo lo relacionado con él del " +
      "servidor. No se podrá recuperar ni descargar.",
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
      titulo: "Este archivo no es un PDF",
      texto:
        "Solo se pueden anonimizar documentos PDF. Si es un Word, guárdalo como " +
        "PDF desde Archivo › Guardar como. Si es una foto o un escaneo en " +
        "imagen, conviértelo antes a PDF.",
    },
    invalid_file: {
      titulo: "Este archivo no es un PDF válido",
      texto:
        "Tiene extensión .pdf pero por dentro no lo es, o está dañado. Ábrelo en " +
        "tu ordenador: si se ve bien, imprímelo como PDF y sube esa copia.",
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
