/**
 * Qué idioma se usa, y qué pasa con lo que falte traducido.
 *
 * La interfaz está en castellano.  El valenciano se añade poniendo un `va.js`
 * con las mismas claves que `es.js`: **no hay que tocar ni una pantalla**, que
 * era la condición.  Lo que falte en la traducción cae al castellano, nunca a
 * un hueco vacío, así que una traducción a medias se puede ir completando sin
 * romper nada.
 *
 * El idioma se elige, por este orden: `?idioma=va` en la dirección (útil para
 * enseñarlo sin configurar nada), lo que se eligiera la última vez, y si no,
 * castellano.  Deliberadamente NO se mira el idioma del navegador: en los
 * equipos del ayuntamiento suele estar en castellano aunque la persona
 * prefiera valenciano, y al revés.
 */
import { TEXTOS as ES } from "./es.js";

const CLAVE = "anonimizador.idioma";
export const IDIOMAS = { es: ES };

function elegido() {
  const deLaUrl = new URLSearchParams(location.search).get("idioma");
  if (deLaUrl && IDIOMAS[deLaUrl]) return deLaUrl;
  try {
    const guardado = localStorage.getItem(CLAVE);
    if (guardado && IDIOMAS[guardado]) return guardado;
  } catch {
    /* sin almacenamiento: castellano */
  }
  return "es";
}

/** Mezcla la traducción sobre el castellano, hoja a hoja. */
function conRespaldo(traduccion, base = ES) {
  const salida = Array.isArray(base) ? [...base] : { ...base };
  for (const [clave, valor] of Object.entries(traduccion || {})) {
    const original = base?.[clave];
    salida[clave] =
      valor && typeof valor === "object" && original && typeof original === "object"
        ? conRespaldo(valor, original)
        : valor;
  }
  return salida;
}

export const IDIOMA = elegido();
export const TEXTOS = IDIOMA === "es" ? ES : conRespaldo(IDIOMAS[IDIOMA]);
