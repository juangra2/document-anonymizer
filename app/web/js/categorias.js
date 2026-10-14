/**
 * Cómo se agrupan los tipos de dato en la pantalla.
 *
 * La agrupación la decide la página, no la API: `GET /entity-types` devuelve
 * los 30 tipos planos. Un tipo que la API devuelva y no esté aquí cae en
 * «Otros» con el nombre que dé el servidor, para que nunca quede invisible si
 * mañana se añade uno y nadie lo asigna.
 *
 * Los nombres y descripciones que ve el usuario salen del fichero de textos
 * cuando existen; si no, de la API. Así se traduce sin tocar el servidor.
 */

export const GRUPOS = [
  {
    id: "personales",
    nombre: "Datos personales",
    resumen: "Nombre, DNI, NIE, Seguridad Social, pasaporte, sanidad, lugar de nacimiento",
    tipos: ["PERSON", "ES_DNI", "ES_NIE", "ES_NSS", "PASSPORT", "MEDICAL_ID", "PLACE_OF_BIRTH"],
  },
  {
    id: "bancarios",
    nombre: "Datos bancarios y económicos",
    resumen: "Cuenta, tarjeta, importes, presupuestos de obra, póliza",
    tipos: ["IBAN_CODE", "CREDIT_CARD", "MONEY", "BUDGET_AMOUNT", "POLICY_NUMBER"],
  },
  {
    id: "contacto",
    nombre: "Contacto y dirección",
    resumen: "Domicilio, código postal, teléfono, correo, catastro",
    tipos: ["ADDRESS", "POSTAL_CODE", "PHONE_NUMBER", "EMAIL_ADDRESS", "CADASTRAL_REFERENCE"],
  },
  {
    id: "documentos",
    nombre: "Empresas y documentos",
    resumen: "Empresa, CIF, expediente, registro, verificación, matrícula, códigos",
    tipos: [
      "ORGANIZATION",
      "ES_CIF",
      "CASE_NUMBER",
      "REGISTRY_NUMBER",
      "CSV",
      "CERT_SERIAL",
      "LICENSE_PLATE",
      "QR_CODE",
    ],
  },
  {
    id: "fechas",
    nombre: "Fechas",
    resumen: "Fecha de nacimiento y el resto de fechas",
    tipos: ["DATE_OF_BIRTH", "DATE_TIME"],
  },
  {
    id: "tecnicos",
    nombre: "Datos técnicos",
    resumen: "Dirección web, IP, usuario",
    tipos: ["URL", "IP_ADDRESS", "USER_ID"],
  },
  {
    id: "tablas",
    nombre: "Filas de tabla",
    resumen: "Filas completas de tablas de interesados y de cargos",
    tipos: ["CASE_TABLE_ROW", "ROLE_TABLE_ROW"],
  },
];

/** Tipos cuyo contenido puede llevar otros tipos dentro: necesitan su aviso. */
export const TIPO_CODIGOS = "QR_CODE";

/**
 * Reparte el catálogo de la API en los grupos de arriba.
 * @param {Array<{id: string, label: string, description: string}>} catalogo
 */
export function agrupar(catalogo) {
  const porId = new Map(catalogo.map((t) => [t.id, t]));
  const usados = new Set();

  const grupos = GRUPOS.map((grupo) => {
    const tipos = grupo.tipos
      .filter((id) => porId.has(id))
      .map((id) => {
        usados.add(id);
        return porId.get(id);
      });
    return { ...grupo, tipos };
  }).filter((grupo) => grupo.tipos.length > 0);

  const sobrantes = catalogo.filter((t) => !usados.has(t.id));
  if (sobrantes.length) {
    grupos.push({
      id: "otros",
      nombre: "Otros",
      resumen: "Tipos que el servicio anonimiza y aún no están clasificados aquí",
      tipos: sobrantes,
    });
  }
  return grupos;
}
