/**
 * De código de la API a frase para una persona.
 *
 * Es la pieza que decide lo que lee quien está delante cuando algo falla, así
 * que la regla es: decir QUÉ ha pasado y QUÉ puede hacer. Nunca se enseña el
 * mensaje técnico del servidor; para soporte va una referencia corta, que son
 * las ocho primeras cifras del identificador (el identificador completo sirve
 * para descargar el documento durante su retención).
 */
import { TEXTOS } from "./texts/index.js";

export function referenciaDeSoporte(idTrabajo) {
  if (!idTrabajo) return "—";
  const limpio = String(idTrabajo).replace(/-/g, "").toUpperCase();
  return `${limpio.slice(0, 4)}-${limpio.slice(4, 8)}`;
}

/**
 * @returns {{titulo: string, texto: string}}
 */
export function frase(codigo, datos = {}) {
  const catalogo = TEXTOS.errores;
  const entrada = catalogo[codigo] || catalogo._generico;
  const contexto = {
    limite: datos.limit_pages ?? datos.limit_mb ?? "",
    tamano: datos.tamano ?? "",
    referencia: referenciaDeSoporte(datos.idTrabajo),
    ...datos,
  };
  return {
    titulo: typeof entrada.titulo === "function" ? entrada.titulo(contexto) : entrada.titulo,
    texto: typeof entrada.texto === "function" ? entrada.texto(contexto) : entrada.texto,
  };
}

/**
 * Un documento que desaparece del servidor significa cosas distintas según lo
 * que estuviera haciendo: si iba a medias, el servicio se reinició; si ya había
 * terminado, es que caducó o alguien lo borró. La frase cambia con eso.
 */
export function faltaEnElServidor(estadoAnterior) {
  return estadoAnterior === "running" || estadoAnterior === "queued"
    ? "servicio_reiniciado"
    : "ya_no_existe";
}
