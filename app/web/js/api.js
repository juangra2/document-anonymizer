/**
 * El ÚNICO fichero que habla con el servidor.
 *
 * Todo va por direcciones relativas: si mañana el servicio cuelga de
 * /anonimizador, o el proxy pide usuario y contraseña, esto sigue funcionando
 * sin tocar nada.
 *
 * Convierte cualquier respuesta fallida en un objeto `ErrorApi` con forma fija
 * —código estable, datos y si se puede reintentar—, para que el resto de la
 * página no vea números HTTP ni textos técnicos del servidor.
 */

export class ErrorApi extends Error {
  constructor(codigo, { estado = 0, datos = {}, reintentable = false } = {}) {
    super(codigo);
    this.codigo = codigo;
    this.estado = estado;
    this.datos = datos;
    this.reintentable = reintentable;
  }
}

const TIEMPO_ESPERA_MS = 15000;

async function pedir(ruta, opciones = {}) {
  const control = new AbortController();
  const reloj = setTimeout(() => control.abort(), opciones.espera ?? TIEMPO_ESPERA_MS);
  try {
    const respuesta = await fetch(ruta, { ...opciones, signal: control.signal });
    if (!respuesta.ok) throw await comoError(respuesta);
    return respuesta;
  } catch (error) {
    if (error instanceof ErrorApi) throw error;
    // Sin conexión, DNS caído, proxy que no responde, o se agotó la espera.
    throw new ErrorApi("sin_conexion", { reintentable: true });
  } finally {
    clearTimeout(reloj);
  }
}

async function comoError(respuesta) {
  let detalle = null;
  try {
    detalle = (await respuesta.json()).detail;
  } catch {
    detalle = null;
  }
  // La API devuelve el detalle como texto o como objeto con código; lo segundo
  // es lo que permite traducir sin leer frases del servidor.
  const codigoApi = detalle && typeof detalle === "object" ? detalle.code : null;
  const datos = detalle && typeof detalle === "object" ? detalle : {};

  const porEstado = {
    404: "ya_no_existe",
    410: "caducado",
    413: "document_too_large",
    415: "noEsPdf",
    422: "pagina_antigua",
    503: "cola_llena",
  };
  const codigo = codigoApi || porEstado[respuesta.status] || "_generico";
  return new ErrorApi(codigo, {
    estado: respuesta.status,
    datos,
    reintentable: respuesta.status >= 500 && respuesta.status !== 503,
  });
}

export async function estadoDelServicio() {
  const respuesta = await pedir("health", { espera: 8000 });
  return respuesta.json();
}

export async function tiposDeDato() {
  const respuesta = await pedir("entity-types");
  return respuesta.json();
}

/**
 * Sube un documento. Usa XMLHttpRequest y no fetch porque es lo único que
 * informa del progreso de SUBIDA: con expedientes de 200 MB, una barra que no
 * se mueve durante minutos parece que se ha colgado.
 */
export function subirDocumento(fichero, tipos, alProgresar) {
  return new Promise((resolver, rechazar) => {
    const parametros = [
      ...(tipos.tratamiento === "metadata_only" ? ["treatment=metadata_only"] : []),
      ...tipos.desactivar.map((t) => `disabled_types=${encodeURIComponent(t)}`),
      ...tipos.activar.map((t) => `enabled_types=${encodeURIComponent(t)}`),
    ].join("&");
    const peticion = new XMLHttpRequest();
    peticion.open("POST", parametros ? `jobs?${parametros}` : "jobs");

    peticion.upload.onprogress = (evento) => {
      if (evento.lengthComputable && alProgresar) {
        alProgresar(Math.round((100 * evento.loaded) / evento.total));
      }
    };
    peticion.onload = () => {
      if (peticion.status === 202) {
        resolver(JSON.parse(peticion.responseText));
        return;
      }
      let detalle = null;
      try {
        detalle = JSON.parse(peticion.responseText).detail;
      } catch {
        detalle = null;
      }
      const datos = detalle && typeof detalle === "object" ? detalle : {};
      const porEstado = {
        400: "invalid_file",
        413: "document_too_large",
        415: "noEsPdf",
        422: "pagina_antigua",
        503: "cola_llena",
      };
      rechazar(
        new ErrorApi(datos.code || porEstado[peticion.status] || "_generico", {
          estado: peticion.status,
          datos,
        }),
      );
    };
    peticion.onerror = () => rechazar(new ErrorApi("subida_interrumpida"));
    peticion.onabort = () => rechazar(new ErrorApi("subida_interrumpida"));

    const cuerpo = new FormData();
    cuerpo.append("file", fichero);
    peticion.send(cuerpo);
  });
}

export async function estadoDelTrabajo(id) {
  const respuesta = await pedir(`jobs/${encodeURIComponent(id)}`);
  return respuesta.json();
}

export async function informeDelTrabajo(id) {
  const respuesta = await pedir(`jobs/${encodeURIComponent(id)}/report`);
  return respuesta.json();
}

/**
 * Guarda el informe como fichero.
 *
 * Es para auditoría y para soporte, no para quien anonimiza: lo que le
 * interesa a él —páginas, cuántos datos, si hay que revisar— ya se lee en la
 * pantalla de resultado.  El informe NO lleva los valores detectados: la cola
 * no admite pedirlos.
 */
export async function descargarInforme(id, nombre) {
  const informe = await informeDelTrabajo(id);
  const contenido = new Blob([JSON.stringify(informe, null, 2)], {
    type: "application/json",
  });
  const enlace = document.createElement("a");
  enlace.href = URL.createObjectURL(contenido);
  enlace.download = nombre;
  document.body.appendChild(enlace);
  enlace.click();
  enlace.remove();
  setTimeout(() => URL.revokeObjectURL(enlace.href), 30000);
}

/** El informe en PDF para leer, con el nombre que la página tiene en memoria. */
export async function descargarInformePdf(id, nombre) {
  const respuesta = await pedir(`jobs/${encodeURIComponent(id)}/report.pdf`, { espera: 60000 });
  const contenido = await respuesta.blob();
  const enlace = document.createElement("a");
  enlace.href = URL.createObjectURL(contenido);
  enlace.download = nombre;
  document.body.appendChild(enlace);
  enlace.click();
  enlace.remove();
  setTimeout(() => URL.revokeObjectURL(enlace.href), 30000);
}

/** Parar y entregar lo procesado. */
export async function pararTrabajo(id) {
  await pedir(`jobs/${encodeURIComponent(id)}/stop`, { method: "POST" });
}

/** Eliminar: parar y borrar todo, sin entregar nada. */
export async function eliminarTrabajo(id) {
  await pedir(`jobs/${encodeURIComponent(id)}`, { method: "DELETE" });
}

/** Dirección de descarga directa. Es el respaldo y lo que usa quien llama a la API. */
export function direccionDeDescarga(id, formato = "pdf") {
  return `jobs/${encodeURIComponent(id)}/result?output=${formato}`;
}

/**
 * Descarga poniéndole el nombre que la página tiene en memoria.
 *
 * El servidor no conoce el nombre del fichero que subió el usuario —no lo
 * guarda a propósito— y sirve la descarga como `documento_anonimizado.pdf`.
 * Ese nombre de la cabecera manda sobre el atributo `download` de un enlace,
 * así que para que el usuario reciba SU nombre hay que traerse el documento y
 * guardarlo desde aquí.
 *
 * Si algo falla (memoria, red), se cae al enlace directo: mejor una descarga
 * con nombre genérico que ninguna descarga.
 */
export async function descargarConNombre(id, nombre, formato = "pdf") {
  const direccion = direccionDeDescarga(id, formato);
  try {
    const respuesta = await pedir(direccion, { espera: 120000 });
    const contenido = await respuesta.blob();
    const enlace = document.createElement("a");
    enlace.href = URL.createObjectURL(contenido);
    enlace.download = nombre;
    document.body.appendChild(enlace);
    enlace.click();
    enlace.remove();
    setTimeout(() => URL.revokeObjectURL(enlace.href), 30000);
  } catch (error) {
    window.location.href = direccion;
  }
}
