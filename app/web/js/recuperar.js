/**
 * Pestaña «Recuperar datos»: un texto con etiquetas, su tabla y la contraseña.
 *
 * Tres pasos, como la pestaña de anonimizar: el texto (pegado o un fichero),
 * la tabla cifrada que se descargó al seudonimizar y la contraseña.  Nada se
 * guarda: ni aquí ni en el servidor.  Lo elegido (archivo, tabla) se puede
 * quitar con su botón ×.
 */
import * as api from "./api.js";
import { frase } from "./errores.js";
import { TEXTOS as T } from "./texts/index.js";

const $ = (id) => document.getElementById(id);
const R = T.recuperar;

const estado = { fichero: null, tabla: null, nombreTabla: "", resultado: null };

export function iniciar() {
  $("recTitulo1").textContent = R.paso1;
  $("recAyuda1").textContent = R.ayuda1;
  $("recTexto").placeholder = R.pegar;
  $("recElegirFichero").textContent = R.elegirFichero;
  $("recTitulo2").textContent = R.paso2;
  $("recAyuda2").textContent = R.ayuda2;
  $("recElegirTabla").textContent = R.elegirTabla;
  $("recTitulo3").textContent = R.paso3;
  $("recClaveEtiqueta").textContent = R.contrasena;
  $("recLanzar").textContent = R.lanzar;
  $("recTituloResultado").textContent = R.resultado;
  $("recCopiar").textContent = R.copiar;
  $("recDescargar").textContent = R.descargar;

  $("recElegirFichero").onclick = () => {
    $("recFichero").value = "";
    $("recFichero").click();
  };
  $("recFichero").onchange = (e) => {
    estado.fichero = e.target.files[0] || null;
    pintar();
  };
  $("recQuitarFichero").setAttribute("aria-label", R.quitarFichero);
  $("recQuitarFichero").title = R.quitarFichero;
  $("recQuitarFichero").onclick = () => {
    estado.fichero = null;
    $("recFichero").value = "";
    pintar();
    $("recElegirFichero").focus();
  };
  $("recQuitarTabla").setAttribute("aria-label", R.quitarTabla);
  $("recQuitarTabla").title = R.quitarTabla;
  $("recQuitarTabla").onclick = () => {
    estado.tabla = null;
    estado.nombreTabla = "";
    $("recTabla").value = "";
    pintar();
    $("recElegirTabla").focus();
  };
  $("recElegirTabla").onclick = () => {
    $("recTabla").value = "";
    $("recTabla").click();
  };
  $("recTabla").onchange = (e) => {
    const fichero = e.target.files[0];
    if (fichero) ponerTabla(fichero, fichero.name);
  };
  $("recTexto").oninput = pintar;
  $("recClave").oninput = pintar;
  $("recLanzar").onclick = lanzar;
  $("recCopiar").onclick = async () => {
    try {
      await navigator.clipboard.writeText(estado.resultado?.text || "");
      $("recCopiar").textContent = R.copiado;
      setTimeout(() => ($("recCopiar").textContent = R.copiar), 2000);
    } catch {
      /* sin permiso de portapapeles: el texto sigue en pantalla para copiarlo a mano */
    }
  };
  $("recDescargar").onclick = descargar;
  pintar();
}

function ponerTabla(contenido, nombre) {
  estado.tabla = contenido;
  estado.nombreTabla = nombre;
  pintar();
}

function pintar() {
  const hayFichero = Boolean(estado.fichero);
  $("recTexto").classList.toggle("hidden", hayFichero);
  $("recFicheroElegido").classList.toggle("hidden", !hayFichero);
  $("recFicheroNombre").textContent = hayFichero ? R.ficheroElegido(estado.fichero.name) : "";
  $("recTablaNombre").textContent = estado.tabla ? R.tablaElegida(estado.nombreTabla) : R.sinTabla;
  $("recQuitarTabla").classList.toggle("hidden", !estado.tabla);
  const listo =
    (hayFichero || $("recTexto").value.trim()) && estado.tabla && $("recClave").value.length > 0;
  $("recLanzar").disabled = !listo;
}

async function lanzar() {
  $("recError").classList.add("hidden");
  $("recLanzar").disabled = true;
  const cuerpo = new FormData();
  cuerpo.append("table", estado.tabla, "tabla.tabla");
  cuerpo.append("password", $("recClave").value);
  if (estado.fichero) cuerpo.append("file", estado.fichero);
  else cuerpo.append("text", $("recTexto").value);
  try {
    estado.resultado = await api.reconstruir(cuerpo);
    estado.nombreOriginal = estado.fichero?.name || null;
    pintarResultado();
  } catch (error) {
    const f = frase(error.codigo, error.datos);
    $("recErrorTexto").textContent = `${f.titulo}. ${f.texto}`;
    $("recError").classList.remove("hidden");
    $("recResultado").classList.add("hidden");
  }
  pintar();
}

function lista(id, titulo, lineas) {
  const caja = $(id);
  caja.textContent = "";
  caja.classList.toggle("hidden", lineas.length === 0);
  if (!lineas.length) return;
  const cabecera = document.createElement("strong");
  cabecera.textContent = titulo;
  caja.appendChild(cabecera);
  const ul = document.createElement("ul");
  lineas.forEach((linea) => {
    const li = document.createElement("li");
    li.textContent = linea;
    ul.appendChild(li);
  });
  caja.appendChild(ul);
}

function pintarResultado() {
  const r = estado.resultado;
  $("recResultado").classList.remove("hidden");
  $("recResumen").textContent = r.exact ? R.exacta(r.resolved) : R.tolerante(r.resolved);
  $("recResumen").className = `rec-resumen${r.unresolved.length ? " warn" : ""}`;
  lista("recNoResueltas", R.noResueltas, r.unresolved);
  lista("recAlteradas", R.alteradas, r.altered.map((a) => R.alterada(a.form, a.label)));
  lista("recNoUsadas", R.noUsadas, r.unused.length ? [r.unused.join(", ")] : []);
  const conTexto = typeof r.text === "string";
  $("recVista").classList.toggle("hidden", !conTexto);
  $("recVista").textContent = conTexto ? r.text : "";
  $("recCopiar").classList.toggle("hidden", !conTexto);
  $("recSinVista").classList.toggle("hidden", conTexto);
  $("recSinVista").textContent = R.sinVista;
  $("recResultado").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function descargar() {
  const r = estado.resultado;
  if (!r) return;
  let contenido;
  let nombre;
  if (r.file_base64) {
    const binario = atob(r.file_base64);
    const bytes = new Uint8Array(binario.length);
    for (let i = 0; i < binario.length; i += 1) bytes[i] = binario.charCodeAt(i);
    contenido = new Blob([bytes], { type: r.media_type || "application/octet-stream" });
    const extension = (r.filename || "").split(".").pop();
    nombre = estado.nombreOriginal
      ? `${estado.nombreOriginal.replace(/(_seudonimizado)?\.[^.]+$/, "")}_recuperado.${extension}`
      : r.filename;
  } else {
    contenido = new Blob([r.text], { type: "text/plain;charset=utf-8" });
    nombre = "texto_recuperado.txt";
  }
  const enlace = document.createElement("a");
  enlace.href = URL.createObjectURL(contenido);
  enlace.download = nombre;
  document.body.appendChild(enlace);
  enlace.click();
  enlace.remove();
  setTimeout(() => URL.revokeObjectURL(enlace.href), 30000);
}
