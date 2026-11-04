/**
 * Subir en fila y seguir el avance de cada documento.
 *
 * Reglas que vienen de cómo se comporta el servidor:
 *
 * * **De uno en uno.** Cinco subidas a la vez se estorban y la barra de
 *   progreso se vuelve mentira; además el servidor procesa un documento cada
 *   vez, así que adelantar subidas no gana nada.
 * * **Nunca dos consultas a la vez** para el mismo documento: la siguiente se
 *   programa cuando llega la respuesta de la anterior.
 * * **No se rinde mientras la página esté abierta.** El documento se procesa en
 *   el servidor pase lo que pase con la conexión, así que un fallo de red
 *   espera cada vez un poco más (2, 4, 8, 16 y luego 30 s) y sigue.
 * * **«No existe» y «caducado» no se reintentan**: son respuestas definitivas.
 */
import * as almacen from "./almacen.js";
import * as api from "./api.js";
import { faltaEnElServidor } from "./errores.js";

const CADENCIA = { queued: 5000, running: 2000, oculto: 15000 };
const ESPERAS_TRAS_FALLO = [2000, 4000, 8000, 16000, 30000];
const FALLOS_PARA_AVISAR = 3;
const MS_PAGINA_LENTA = 3 * 60 * 1000;

export class Seguimiento {
  /**
   * @param {(documento) => void} alCambiar  se llama con cada cambio de estado
   * @param {(documento, aviso) => void} alAvisar  aviso de página en vivo
   */
  constructor(alCambiar, alAvisar) {
    this.alCambiar = alCambiar;
    this.alAvisar = alAvisar;
    this.documentos = [];
    this.subiendo = false;
    this.avisosVistos = new Map();
  }

  añadir(documento) {
    this.documentos.push(documento);
    this.alCambiar(documento);
  }

  /** Sube los que estén pendientes, uno detrás de otro. */
  async subirPendientes(tipos) {
    if (this.subiendo) return;
    this.subiendo = true;
    try {
      for (const documento of this.documentos) {
        if (documento.estado !== "pendiente") continue;
        await this._subirUno(documento, tipos);
      }
    } finally {
      this.subiendo = false;
    }
  }

  async _subirUno(documento, tipos) {
    documento.estado = "subiendo";
    documento.porcentajeSubida = 0;
    this.alCambiar(documento);
    try {
      const trabajo = await api.subirDocumento(
        documento.fichero,
        tipos,
        (porcentaje) => {
          documento.porcentajeSubida = porcentaje;
          this.alCambiar(documento);
        },
      );
      documento.id = trabajo.job_id;
      documento.subido = Date.now();
      documento.estado = trabajo.status;
      // Se recuerda YA: si cierran la pestaña ahora mismo, al volver el
      // documento sigue ahí, con su nombre (solo en este navegador).
      almacen.recordar(trabajo.job_id, { nombre: documento.nombre });
      documento.trabajo = trabajo;
      documento.fichero = null; // ya está en el servidor; no lo retenemos
      this.alCambiar(documento);
      this.vigilar(documento);
    } catch (error) {
      documento.estado = "error";
      documento.error = { codigo: error.codigo, datos: error.datos };
      this.alCambiar(documento);
    }
  }

  /** Un documento que ya no se sigue tampoco se recuerda. */
  olvidar(documento) {
    if (documento.id) almacen.olvidar(documento.id);
  }

  /** Empieza (o retoma) las consultas periódicas de un documento. */
  vigilar(documento) {
    if (documento.temporizador) clearTimeout(documento.temporizador);
    documento.fallos = 0;
    this._consultar(documento);
  }

  async _consultar(documento) {
    if (!documento.id) return;
    try {
      const estado = await api.estadoDelTrabajo(documento.id);
      documento.fallos = 0;
      documento.sinConexion = false;
      this._aplicar(documento, estado);
    } catch (error) {
      if (error.codigo === "ya_no_existe") {
        // Ya no está en el servidor: el navegador lo olvida, nombre incluido.
        almacen.olvidar(documento.id);
        documento.estado = "error";
        documento.error = { codigo: faltaEnElServidor(documento.estado), datos: {} };
        this.alCambiar(documento);
        return;
      }
      documento.fallos = (documento.fallos || 0) + 1;
      documento.sinConexion = documento.fallos >= FALLOS_PARA_AVISAR;
      this.alCambiar(documento);
    }
    this._programar(documento);
  }

  _aplicar(documento, estado) {
    const anterior = documento.trabajo || {};
    documento.trabajo = estado;
    documento.estado = estado.status;
    // Caducado en el servidor: el navegador lo olvida, nombre incluido.
    if (estado.status === "expired") almacen.olvidar(documento.id);

    // Avisos nuevos desde la última consulta: se enseñan una sola vez.
    const vistos = this.avisosVistos.get(documento.id) || 0;
    const nuevos = (estado.notices || []).slice(vistos);
    if (nuevos.length) {
      this.avisosVistos.set(documento.id, vistos + nuevos.length);
      nuevos.forEach((aviso) => this.alAvisar(documento, aviso));
    }

    // Página que no avanza: no es un error, pero es cuando la gente cree que
    // se ha colgado y llama por teléfono.
    const pagina = estado.progress?.pages_done ?? 0;
    if (pagina !== anterior.progress?.pages_done) {
      documento.paginaDesde = Date.now();
      documento.paginaLenta = false;
    } else if (
      estado.status === "running" &&
      documento.paginaDesde &&
      Date.now() - documento.paginaDesde > MS_PAGINA_LENTA
    ) {
      documento.paginaLenta = true;
    }
    this.alCambiar(documento);
  }

  _programar(documento) {
    const terminado = ["completed", "failed", "expired", "error"].includes(documento.estado);
    if (terminado) return;

    let espera;
    if (documento.fallos) {
      espera = ESPERAS_TRAS_FALLO[Math.min(documento.fallos - 1, ESPERAS_TRAS_FALLO.length - 1)];
    } else if (document.hidden) {
      espera = CADENCIA.oculto;
    } else {
      espera = CADENCIA[documento.estado] || CADENCIA.running;
    }
    documento.temporizador = setTimeout(() => this._consultar(documento), espera);
  }

  /** Al volver a la pestaña se pregunta en el acto, sin esperar el turno. */
  despertar() {
    this.documentos.forEach((documento) => {
      if (["queued", "running"].includes(documento.estado)) this.vigilar(documento);
    });
  }

  quitar(documento) {
    if (documento.temporizador) clearTimeout(documento.temporizador);
    this.documentos = this.documentos.filter((d) => d !== documento);
  }
}
