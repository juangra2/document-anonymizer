/**
 * Lo que el navegador recuerda entre visitas: identificador, hora, páginas y el
 * nombre del fichero, para que al volver la lista diga qué documento es.
 *
 * El nombre puede llevar el del interesado, así que solo se guarda mientras el
 * documento sigue en el servidor: se borra cuando caduca (a las seis horas,
 * como el resultado), al pulsar Eliminar o Quitar, y cuando el servidor dice
 * que el trabajo ya no existe.  Nunca llega al servidor.
 *
 * Si el navegador no deja guardar nada (modo privado, políticas del equipo),
 * la página funciona igual: solo se pierde la recuperación al volver.
 */

const CLAVE = "anonimizador.trabajos";
const MAXIMO = 10;
// Lo que el servidor conserva el resultado (RESULT_TTL_MINUTES, seis horas).
const CADUCA_MS = 6 * 60 * 60 * 1000;

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

export function recordar(id, { paginas = 0, nombre = null } = {}) {
  const lista = leerCrudo().filter((t) => t.id !== id);
  lista.unshift({ id, subido: Date.now(), paginas, nombre });
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
