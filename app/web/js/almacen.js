/**
 * Lo que el navegador recuerda entre visitas.
 *
 * Solo identificadores, hora y páginas. **Nunca el nombre del fichero**: en el
 * ayuntamiento los nombres de expediente suelen llevar el del interesado y
 * estos equipos pueden ser compartidos. Mientras la pestaña sigue abierta el
 * nombre se ve, porque está en memoria; al volver después de cerrarla, la
 * lista enseña la hora y el número de páginas.
 *
 * Si el navegador no deja guardar nada (modo privado, políticas del equipo),
 * la página funciona igual: solo se pierde la recuperación al volver.
 */

const CLAVE = "anonimizador.trabajos";
const MAXIMO = 10;
const CADUCA_MS = 24 * 60 * 60 * 1000;

function leerCrudo() {
  try {
    return JSON.parse(localStorage.getItem(CLAVE) || "[]");
  } catch {
    return [];
  }
}

function escribir(lista) {
  try {
    localStorage.setItem(CLAVE, JSON.stringify(lista.slice(0, MAXIMO)));
  } catch {
    /* sin almacenamiento: la página sigue funcionando sin recuperación */
  }
}

export function recordar(id, { paginas = 0 } = {}) {
  const lista = leerCrudo().filter((t) => t.id !== id);
  lista.unshift({ id, subido: Date.now(), paginas });
  escribir(lista);
}

export function actualizar(id, cambios) {
  const lista = leerCrudo().map((t) => (t.id === id ? { ...t, ...cambios } : t));
  escribir(lista);
}

export function olvidar(id) {
  escribir(leerCrudo().filter((t) => t.id !== id));
}

/** Los recordados que aún tienen sentido consultar. */
export function recordados() {
  const limite = Date.now() - CADUCA_MS;
  const vigentes = leerCrudo().filter((t) => t.subido > limite);
  if (vigentes.length !== leerCrudo().length) escribir(vigentes);
  return vigentes;
}
