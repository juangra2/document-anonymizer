/**
 * Arranque y los tres pasos de la pantalla.
 *
 * Este fichero solo orquesta: quien habla con el servidor es `api.js`, quien
 * traduce los fallos es `errores.js`, las frases están en `texts/` y la
 * agrupación de tipos en `categorias.js`.
 */
import * as api from "./api.js";
import * as almacen from "./almacen.js";
import { agrupar, TIPO_CODIGOS } from "./categorias.js";
import { frase, referenciaDeSoporte } from "./errores.js";
import { Seguimiento } from "./seguimiento.js";
import { TEXTOS as T } from "./texts/es.js";

const MAX_TANDA = 10;
const AVISO_TANDA_MB = 500;

const estado = {
  catalogo: [],
  grupos: [],
  desmarcados: new Set(),
  abierto: null,
  limites: { max_upload_mb: 200, max_pdf_pages: 300 },
  servicio: "desconocido",
  cola: 0,
};

const $ = (id) => document.getElementById(id);
const seguimiento = new Seguimiento(pintarSeguimiento, avisarPagina);

/* ------------------------------------------------------------------ arranque */

async function arrancar() {
  $("titulo").textContent = T.app.titulo;
  $("organizacion").textContent = T.app.organizacion;
  $("ayudaTipos").textContent = T.paso2.ayuda;
  $("vacio").textContent = T.paso3.vacio;
  $("retencion").textContent = T.paso3.retencion;
  $("anonimizar").textContent = T.paso3.anonimizar;
  $("elegir").textContent = T.paso1.elegir;
  $("zonaTitulo").textContent = T.paso1.soltar;
  $("nuncaTitulo").textContent = T.paso2.nuncaSeOculta;
  T.paso2.nuncaSeOcultaDetalle.forEach((linea) => {
    const li = document.createElement("li");
    li.textContent = linea;
    $("nuncaLista").appendChild(li);
  });

  conectarEventos();
  await comprobarServicio();
  setInterval(comprobarServicio, 60000);
  await cargarTipos();
  await recuperarRecordados();
}

function conectarEventos() {
  $("elegir").onclick = () => {
    // Se limpia AQUÍ, no después de elegir: vaciar el selector cuando ya
    // tenemos los ficheros en memoria invalida su contenido y se subirían
    // vacíos.  Limpiarlo al abrirlo permite además reelegir el mismo fichero.
    $("ficheros").value = "";
    $("ficheros").click();
  };
  $("ficheros").onchange = (e) => añadirFicheros([...e.target.files]);
  $("anonimizar").onclick = lanzarTanda;
  $("avisoCerrar").onclick = () => $("aviso").classList.add("hidden");

  const zona = $("zona");
  ["dragenter", "dragover"].forEach((evento) =>
    zona.addEventListener(evento, (e) => {
      e.preventDefault();
      zona.classList.add("dragging");
    }),
  );
  ["dragleave", "drop"].forEach((evento) =>
    zona.addEventListener(evento, (e) => {
      e.preventDefault();
      zona.classList.remove("dragging");
    }),
  );
  zona.addEventListener("drop", (e) => añadirFicheros([...e.dataTransfer.files]));

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) seguimiento.despertar();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") $("confirmacion").classList.add("hidden");
  });
}

/* -------------------------------------------------------- estado del servicio */

async function comprobarServicio() {
  try {
    const salud = await api.estadoDelServicio();
    estado.limites = salud.limits || estado.limites;
    estado.cola = salud.queue?.queued ?? 0;
    estado.servicio = salud.ready ? (salud.status === "ok" ? "ok" : "lento") : "caido";
  } catch {
    estado.servicio = "sinConexion";
  }
  pintarServicio();
}

function pintarServicio() {
  const chip = $("salud");
  const banner = $("avisoServicio");
  chip.classList.remove("warn", "down");
  banner.classList.add("hidden");

  if (estado.servicio === "ok") {
    $("saludTexto").textContent = T.salud.activo;
  } else if (estado.servicio === "lento") {
    chip.classList.add("warn");
    $("saludTexto").textContent = T.salud.lento;
    mostrarBanner(banner, T.salud.avisoLento, "warn");
  } else {
    chip.classList.add("down");
    $("saludTexto").textContent = T.salud.caido;
    mostrarBanner(
      banner,
      estado.servicio === "sinConexion" ? T.salud.avisoSinConexion : T.salud.avisoCaido,
      "down",
    );
  }

  const bloqueado = estado.servicio === "caido" || estado.servicio === "sinConexion";
  $("zona").classList.toggle("disabled", bloqueado);
  $("anonimizar").disabled = bloqueado;
  $("zonaLimites").textContent = T.paso1.limites(
    MAX_TANDA,
    estado.limites.max_pdf_pages,
    estado.limites.max_upload_mb,
  );
}

function mostrarBanner(elemento, texto, clase) {
  elemento.classList.remove("hidden", "warn", "down");
  elemento.classList.add("banner", clase);
  elemento.querySelector("span:last-child").textContent = texto;
}

/* ------------------------------------------------------------- tipos de dato */

async function cargarTipos() {
  try {
    estado.catalogo = await api.tiposDeDato();
    estado.grupos = agrupar(estado.catalogo);
    pintarGrupos();
  } catch {
    /* el indicador de estado ya explica que el servicio no responde */
  }
}

function pintarGrupos() {
  const contenedor = $("grupos");
  contenedor.textContent = "";

  estado.grupos.forEach((grupo) => {
    const abierto = estado.abierto === grupo.id;
    const apagados = grupo.tipos.filter((t) => estado.desmarcados.has(t.id)).length;
    const marca =
      apagados === 0 ? "" : apagados === grupo.tipos.length ? " off" : " mixed";

    const caja = document.createElement("div");
    caja.className = `grp${abierto ? " open" : ""}`;

    const cabecera = document.createElement("div");
    cabecera.className = "grp-head";
    cabecera.tabIndex = 0;
    cabecera.setAttribute("role", "button");
    cabecera.setAttribute("aria-expanded", String(abierto));
    cabecera.innerHTML =
      `<span class="tick${marca}" data-grupo="${grupo.id}"></span>` +
      `<span class="spacer"><span class="grp-name"></span><span class="desc"></span></span>` +
      `<span class="n">${grupo.tipos.length}</span><span class="chev">&#9660;</span>`;
    cabecera.querySelector(".grp-name").textContent = grupo.nombre;
    cabecera.querySelector(".desc").textContent = grupo.resumen;

    const alternar = (evento) => {
      if (evento?.target?.dataset?.grupo) {
        const todos = grupo.tipos.every((t) => estado.desmarcados.has(t.id));
        grupo.tipos.forEach((t) =>
          todos ? estado.desmarcados.delete(t.id) : estado.desmarcados.add(t.id),
        );
      } else {
        estado.abierto = abierto ? null : grupo.id;
      }
      pintarGrupos();
    };
    cabecera.onclick = alternar;
    cabecera.onkeydown = (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        alternar();
      }
    };
    caja.appendChild(cabecera);

    if (abierto) {
      const cuerpo = document.createElement("div");
      cuerpo.className = "grp-body";
      grupo.tipos.forEach((tipo) => {
        const apagado = estado.desmarcados.has(tipo.id);
        const fila = document.createElement("div");
        fila.className = `row${apagado ? " off" : ""}`;
        fila.tabIndex = 0;
        fila.innerHTML =
          `<span class="tick${apagado ? " off" : ""}"></span>` +
          `<span><span class="type-name"></span><span class="desc"></span></span>`;
        // textContent y no innerHTML: los nombres vienen del servidor.
        fila.querySelector(".type-name").textContent = tipo.label;
        fila.querySelector(".desc").textContent = tipo.description;
        const pulsar = () => {
          apagado ? estado.desmarcados.delete(tipo.id) : estado.desmarcados.add(tipo.id);
          pintarGrupos();
        };
        fila.onclick = pulsar;
        fila.onkeydown = (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            pulsar();
          }
        };
        cuerpo.appendChild(fila);

        if (tipo.id === TIPO_CODIGOS && apagado) {
          const aviso = document.createElement("div");
          aviso.className = "qr-warn";
          aviso.textContent = T.paso2.avisoQr;
          cuerpo.appendChild(aviso);
        }
      });
      caja.appendChild(cuerpo);
    }
    contenedor.appendChild(caja);
  });

  const activos = estado.catalogo.length - estado.desmarcados.size;
  $("activos").textContent = T.paso2.activos(activos, estado.catalogo.length);

  const visibles = $("visibles");
  if (estado.desmarcados.size) {
    const nombres = estado.catalogo
      .filter((t) => estado.desmarcados.has(t.id))
      .map((t) => t.label.toLowerCase())
      .join(", ");
    visibles.textContent = T.paso2.quedanVisibles(nombres);
    visibles.classList.remove("hidden");
  } else {
    visibles.classList.add("hidden");
  }
}

/* ----------------------------------------------------------------- documentos */

function añadirFicheros(ficheros) {
  const hueco = MAX_TANDA - seguimiento.documentos.length;
  ficheros.slice(0, Math.max(0, hueco)).forEach((fichero) => {
    const documento = { fichero, nombre: fichero.name, estado: "pendiente" };
    const problema = validarEnLocal(fichero);
    if (problema) {
      documento.estado = "error";
      documento.error = problema;
    }
    seguimiento.añadir(documento);
  });
  avisarSiTandaGrande();
  pintarSeguimiento();
}

/** Lo que se puede rechazar sin molestar al servidor: tipo y tamaño. */
function validarEnLocal(fichero) {
  const esPdf =
    fichero.type === "application/pdf" || fichero.name.toLowerCase().endsWith(".pdf");
  if (!esPdf) return { codigo: "noEsPdf", datos: {} };

  const mb = fichero.size / (1024 * 1024);
  if (mb > estado.limites.max_upload_mb) {
    return {
      codigo: "document_too_large",
      datos: { tamano: Math.round(mb), limit_mb: estado.limites.max_upload_mb },
    };
  }
  return null;
}

function avisarSiTandaGrande() {
  const total = seguimiento.documentos
    .filter((d) => d.fichero)
    .reduce((suma, d) => suma + d.fichero.size, 0);
  const mb = Math.round(total / (1024 * 1024));
  const aviso = $("avisoTanda");
  if (mb >= AVISO_TANDA_MB) {
    aviso.classList.remove("hidden");
    $("avisoTandaTexto").textContent = T.paso1.tandaGrande(mb);
  } else {
    aviso.classList.add("hidden");
  }
}

async function lanzarTanda() {
  $("anonimizar").disabled = true;
  await seguimiento.subirPendientes([...estado.desmarcados]);
  $("anonimizar").disabled = false;
  pintarSeguimiento();
}

async function recuperarRecordados() {
  for (const recordado of almacen.recordados()) {
    const documento = {
      id: recordado.id,
      nombre: null, // el nombre no se guarda: puede llevar el del interesado
      subido: recordado.subido,
      paginas: recordado.paginas,
      estado: "queued",
    };
    seguimiento.añadir(documento);
    seguimiento.vigilar(documento);
  }
}

/* ------------------------------------------------------------------- pintado */

function nombreVisible(documento) {
  if (documento.nombre) return documento.nombre;
  const hora = new Date(documento.subido || Date.now()).toLocaleTimeString("es-ES", {
    hour: "2-digit",
    minute: "2-digit",
  });
  const paginas = documento.trabajo?.progress?.pages_total || documento.paginas || 0;
  return paginas
    ? `Documento de las ${hora} · ${paginas} páginas`
    : `Documento de las ${hora}`;
}

function pintarSeguimiento() {
  const documentos = seguimiento.documentos;
  $("seguimiento").classList.toggle("hidden", documentos.length === 0);
  $("vacio").classList.toggle("hidden", documentos.length > 0);
  $("anonimizar").classList.toggle(
    "hidden",
    !documentos.some((d) => d.estado === "pendiente"),
  );
  $("seAplica").classList.toggle("hidden", documentos.length === 0);
  $("seAplica").textContent = T.paso2.seAplica(documentos.length);

  const lista = $("lista");
  lista.textContent = "";
  if (!documentos.length) return;

  pintarResumen(documentos);
  pintarEnCurso(documentos);
  documentos.forEach((documento) => lista.appendChild(filaDocumento(documento)));

  documentos.forEach((documento) => {
    if (documento.id && ["queued", "running"].includes(documento.estado)) {
      almacen.actualizar(documento.id, {
        paginas: documento.trabajo?.progress?.pages_total || 0,
      });
    }
  });
}

function pintarResumen(documentos) {
  const cuenta = (prueba) => documentos.filter(prueba).length;
  const partes = [];
  const terminados = cuenta(
    (d) => d.estado === "completed" && !d.trabajo?.result?.partial,
  );
  const parciales = cuenta((d) => d.trabajo?.result?.partial);
  const procesando = cuenta((d) => d.estado === "running");
  const enCola = cuenta((d) => d.estado === "queued");
  const problemas = cuenta((d) => d.estado === "error" || d.estado === "failed");

  if (terminados) partes.push(T.paso3.resumenTerminados(terminados));
  if (parciales) partes.push(T.paso3.resumenParciales(parciales));
  if (procesando) partes.push(T.paso3.resumenProcesando(procesando));
  if (enCola) partes.push(T.paso3.resumenEnCola(enCola));
  if (problemas) partes.push(T.paso3.resumenConProblemas(problemas));
  $("resumen").textContent = T.paso3.resumen(partes);
}

function pintarEnCurso(documentos) {
  const actual = documentos.find((d) => d.estado === "running");
  const caja = $("enCurso");
  if (!actual) {
    caja.classList.add("hidden");
    $("avisoCola").classList.add("hidden");
    return;
  }
  caja.classList.remove("hidden");
  const indice = documentos.indexOf(actual) + 1;
  const avance = actual.trabajo?.progress || {};
  $("enCursoTexto").textContent = avance.pages_total
    ? T.paso3.enCurso(indice, documentos.length, avance.pages_done, avance.pages_total)
    : T.paso3.enCursoSinPaginas(indice, documentos.length);
  $("enCursoPorcentaje").textContent = `${avance.percent ?? 0} %`;
  $("barra").style.width = `${avance.percent ?? 0}%`;

  const porDelante = documentos.filter((d) => d.estado === "queued").length + estado.cola;
  const avisoCola = $("avisoCola");
  avisoCola.classList.toggle("hidden", porDelante < 3);
  $("avisoColaTexto").textContent = T.paso3.colaCargada;
}

function filaDocumento(documento) {
  const fila = document.createElement("div");
  fila.className = "doc";

  const resultado = documento.trabajo?.result;
  const parcial = !!resultado?.partial;
  const icono = document.createElement("span");
  const clases = {
    completed: parcial ? "part" : "done",
    running: "run",
    queued: "wait",
    subiendo: "wait",
    pendiente: "wait",
    error: "err",
    failed: "err",
    expired: "err",
  };
  icono.className = `ico ${clases[documento.estado] || "wait"}`;
  icono.textContent =
    documento.estado === "completed" ? (parcial ? "½" : "✓")
    : documento.estado === "running" ? "●"
    : ["error", "failed", "expired"].includes(documento.estado) ? "!"
    : "⋯";
  fila.appendChild(icono);

  const meta = document.createElement("span");
  meta.className = "meta";
  const nombre = document.createElement("span");
  nombre.className = "name";
  nombre.textContent = nombreVisible(documento);
  meta.appendChild(nombre);

  const linea = document.createElement("span");
  linea.className = "st";
  if (documento.sinConexion) {
    linea.classList.add("err");
    linea.textContent = frase("sin_conexion").texto;
  } else if (documento.estado === "error" || documento.estado === "failed") {
    linea.classList.add("err");
    const codigo = documento.error?.codigo || documento.trabajo?.error?.code || "_generico";
    const datos = { ...(documento.error?.datos || {}), idTrabajo: documento.id };
    const f = frase(codigo, datos);
    linea.textContent = `${f.titulo}. ${f.texto}`;
  } else if (documento.estado === "pendiente") {
    linea.textContent = T.documento.esperandoSubida;
  } else if (documento.estado === "subiendo") {
    linea.textContent = T.documento.subiendo(documento.porcentajeSubida ?? 0);
  } else if (documento.estado === "queued") {
    linea.textContent = T.documento.enCola(documento.trabajo?.queue_position ?? 0);
  } else if (documento.estado === "running") {
    linea.classList.add("run");
    linea.textContent = documento.trabajo?.stopping
      ? T.documento.parando
      : T.documento.procesando(
          documento.trabajo?.progress?.pages_done,
          documento.trabajo?.progress?.pages_total,
        );
  } else if (documento.estado === "completed") {
    if (parcial) {
      linea.classList.add("part");
      linea.textContent = T.documento.parcial(
        resultado.pages,
        resultado.pages_total_original,
      );
    } else {
      linea.textContent = T.documento.terminado(
        resultado?.entities_redacted ?? 0,
        resultado?.pages ?? 0,
      );
    }
  } else if (documento.estado === "expired") {
    linea.classList.add("err");
    linea.textContent = frase("ya_no_existe").texto;
  }
  meta.appendChild(linea);

  const tachadas = documento.trabajo?.notices_total || 0;
  if (tachadas) {
    const nota = document.createElement("span");
    nota.className = "note";
    nota.textContent = T.documento.paginasTachadas(tachadas);
    meta.appendChild(nota);
  }
  if (documento.paginaLenta && documento.estado === "running") {
    const nota = document.createElement("span");
    nota.className = "note";
    nota.textContent = T.paso3.paginaLenta(documento.trabajo?.progress?.pages_done);
    meta.appendChild(nota);
  }
  if (resultado?.manual_review_required && !parcial) {
    const nota = document.createElement("span");
    nota.className = "note";
    nota.textContent = T.documento.revisar;
    meta.appendChild(nota);
  }
  fila.appendChild(meta);
  fila.appendChild(acciones(documento, parcial));
  return fila;
}

function acciones(documento, parcial) {
  const caja = document.createElement("span");
  caja.className = "acts";
  const boton = (texto, clase, alPulsar) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = `btn ${clase}`.trim();
    b.textContent = texto;
    b.onclick = alPulsar;
    caja.appendChild(b);
    return b;
  };

  if (documento.estado === "completed") {
    const descarga = document.createElement("a");
    descarga.className = "btn primary";
    descarga.textContent = parcial ? T.acciones.descargarParcial : T.acciones.descargar;
    descarga.href = api.direccionDeDescarga(documento.id);
    descarga.onclick = (evento) => {
      // El nombre lo pone la página con lo que tiene en memoria: el servidor no
      // conoce el original.  Si no lo sabemos (se recuperó tras cerrar la
      // pestaña), se deja el enlace normal con el nombre genérico.
      if (!documento.nombre) return;
      evento.preventDefault();
      api.descargarConNombre(documento.id, nombreDescarga(documento, parcial));
    };
    caja.appendChild(descarga);
    boton(T.acciones.eliminar, "danger", () => confirmarEliminar(documento));
    return caja;
  }

  if (documento.estado === "running") {
    // Parar solo tiene sentido con al menos una página terminada; antes de eso,
    // lo que se quiere es eliminar.
    const paginas = documento.trabajo?.progress?.pages_done ?? 0;
    if (paginas >= 1 && !documento.trabajo?.stopping) {
      boton(T.acciones.parar, "", async () => {
        try {
          await api.pararTrabajo(documento.id);
        } catch (error) {
          mostrarAviso(frase(error.codigo, { idTrabajo: documento.id }));
        }
      });
    }
    boton(T.acciones.eliminar, "danger", () => confirmarEliminar(documento));
    return caja;
  }

  if (documento.estado === "queued") {
    boton(T.acciones.eliminar, "danger", () => confirmarEliminar(documento));
    return caja;
  }

  if (["error", "failed", "expired"].includes(documento.estado)) {
    boton(T.acciones.quitar, "", () => {
      if (documento.id) almacen.olvidar(documento.id);
      seguimiento.quitar(documento);
      pintarSeguimiento();
    });
  }
  return caja;
}

function nombreDescarga(documento, parcial) {
  const base = documento.nombre.replace(/\.pdf$/i, "");
  if (!parcial) return `${base}_anonimizado.pdf`;
  const r = documento.trabajo.result;
  return `${base}_anonimizado_parcial_1-${r.pages}_de_${r.pages_total_original}.pdf`;
}

/* ------------------------------------------------------------------- diálogos */

let pendienteDeEliminar = null;

function confirmarEliminar(documento) {
  pendienteDeEliminar = documento;
  $("confTitulo").textContent = T.confirmar.eliminarTitulo;
  $("confTexto").textContent = T.confirmar.eliminarTexto;
  $("confirmacion").classList.remove("hidden");
  $("confNo").focus();
  $("confNo").onclick = () => $("confirmacion").classList.add("hidden");
  $("confSi").onclick = async () => {
    $("confirmacion").classList.add("hidden");
    const objetivo = pendienteDeEliminar;
    if (!objetivo) return;
    try {
      if (objetivo.id) {
        await api.eliminarTrabajo(objetivo.id);
        almacen.olvidar(objetivo.id);
      }
    } catch {
      /* si ya no está en el servidor, quitarlo de la lista es lo correcto */
    }
    seguimiento.quitar(objetivo);
    pintarSeguimiento();
  };
}

function avisarPagina(documento, aviso) {
  mostrarAviso({
    titulo: T.avisos.paginaTachadaTitulo,
    texto: T.avisos.paginaTachada(aviso.page, aviso.reason),
  });
}

function mostrarAviso({ titulo, texto }) {
  $("avisoTitulo").textContent = titulo;
  $("avisoTexto").textContent = texto;
  $("aviso").classList.remove("hidden");
}

/* Se exporta para las pruebas de navegador. */
window.__anonimizador = { estado, seguimiento, referenciaDeSoporte };

arrancar();
