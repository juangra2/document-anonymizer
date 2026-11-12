"""Corpus de textos (.txt) y Markdown (.md) con datos inventados y sus etiquetas.

Imita lo que llega en texto a un ayuntamiento: cartas y notificaciones,
formularios de "campo: valor", actas en valenciano, correos exportados,
listados de columnas fijas de un programa antiguo, informes; y en Markdown,
expedientes con encabezados, tablas, enlaces (mailto:, URL con datos, enlaces
automaticos, referencias), imagenes, HTML y codigo.  Cada texto va en una
codificacion (UTF-8 con y sin BOM, UTF-16, Windows-1252, MS-DOS 850) y con
saltos de linea de Windows o de Unix.  Hay textos sin ningun dato, que tienen
que salir identicos.

Etiqueta de cada documento (etiquetas.json):
  datos         lo que no puede quedar en el texto de salida (texto y tipo)
  legitimos     texto que tiene que seguir tal cual
  codificacion  la del fichero (y la de la salida)
  sin_datos     el fichero tiene que salir identico, byte a byte

Uso: python -m evaluation.corpus_texto [DESTINO]   (por defecto evaluation/texto/corpus)
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from evaluation.corpus_imagenes import PERSONAS as PERSONAS_IMAGEN
from evaluation.corpus_word import PERSONAS as PERSONAS_WORD

DESTINO = Path("evaluation/texto/corpus")
P = PERSONAS_WORD + PERSONAS_IMAGEN          # cinco personas inventadas
_BOM = {"utf-8-sig": "utf-8", "utf-16": "utf-16"}


@dataclass
class Caso:
    nombre: str
    formato: str            # txt o md
    codificacion: str       # utf-8, utf-8-sig (con BOM), utf-16 (con BOM), cp1252, cp850
    saltos: str             # "\n" o "\r\n"
    texto: str
    datos: list[dict] = field(default_factory=list)
    legitimos: list[str] = field(default_factory=list)
    sin_datos: bool = False

    def bytes(self) -> bytes:
        return self.texto.replace("\n", self.saltos).encode(self.codificacion)


def _d(texto: str, tipo: str) -> dict:
    return {"text": texto, "type": tipo}


def _persona(p: dict, *campos: str) -> list[dict]:
    tipos = {"nombre": "PERSON", "dni": "ES_DNI", "telefono": "PHONE_NUMBER", "correo": "EMAIL_ADDRESS",
             "direccion": "ADDRESS", "nacimiento": "DATE_OF_BIRTH", "iban": "IBAN_CODE"}
    return [_d(p[c], tipos[c]) for c in campos]


# --- texto plano ------------------------------------------------------------------

def carta_notificacion() -> Caso:
    p = P[0]
    texto = f"""AJUNTAMENT DE VALÈNCIA
Servicio de Licencias Urbanísticas

Destinataria: {p['nombre']}
Domicilio: {p['direccion']}

Asunto: Notificación de resolución

Por la presente se notifica a Doña {p['nombre']}, con DNI {p['dni']}, la resolución
favorable de la licencia de obra menor solicitada para la reforma del baño.

Contra esta resolución, que pone fin a la vía administrativa, puede interponerse
recurso potestativo de reposición en el plazo de un mes.

Para cualquier consulta puede llamar al teléfono {p['telefono']} que consta en la
solicitud o escribir a {p['correo']}.

València, 14 de octubre de 2026
La jefa del Servicio
"""
    return Caso("carta_notificacion", "txt", "cp1252", "\r\n", texto,
                _persona(p, "nombre", "dni", "telefono", "correo", "direccion"),
                ["Servicio de Licencias Urbanísticas", "Notificación de resolución",
                 "recurso potestativo de reposición en el plazo de un mes", "La jefa del Servicio"])


def solicitud_formulario() -> Caso:
    p = P[1]
    texto = f"""SOLICITUD DE AYUDA AL ALQUILER - EJERCICIO 2026

DATOS DEL SOLICITANTE
Nombre y apellidos: {p['nombre']}
DNI/NIE: {p['dni']}
Fecha de nacimiento: {p['nacimiento']}
Domicilio: {p['direccion']}
Teléfono: {p['telefono']}
Correo electrónico: {p['correo']}

DATOS BANCARIOS
IBAN: {p['iban']}

DOCUMENTACIÓN APORTADA
[X] Contrato de arrendamiento
[X] Justificante de ingresos
[ ] Certificado de empadronamiento

Importe solicitado: 1.800,00 euros
"""
    return Caso("solicitud_formulario", "txt", "utf-8", "\n", texto,
                _persona(p, "nombre", "dni", "nacimiento", "direccion", "telefono", "correo", "iban"),
                ["SOLICITUD DE AYUDA AL ALQUILER - EJERCICIO 2026", "DATOS DEL SOLICITANTE",
                 "Contrato de arrendamiento", "Justificante de ingresos"])


def acta_valenciano() -> Caso:
    texto = f"""ACTA DE LA SESSIÓ ORDINÀRIA DE LA JUNTA DE DISTRICTE

Data: 7 de novembre de 2026
Lloc: Centre Municipal de Serveis Socials

Assistents:
- {P[1]['nombre']}, president
- {P[2]['nombre']}, secretària
- {P[3]['nombre']}, vocal

Ordre del dia:
1. Lectura i aprovació de l'acta anterior.
2. Proposta de millora de l'enllumenat del carrer.

Intervé {P[3]['nombre']}, que demana que es revise la il·luminació de la plaça.
La secretària, {P[2]['nombre']}, recorda que el veí amb DNI {P[3]['dni']} va presentar
una queixa el mes passat.

S'alça la sessió a les 20.15 hores.
"""
    return Caso("acta_valenciano", "txt", "cp1252", "\r\n", texto,
                [_d(P[1]["nombre"], "PERSON"), _d(P[2]["nombre"], "PERSON"), _d(P[3]["nombre"], "PERSON"),
                 _d(P[3]["dni"], "ES_DNI")],
                ["ACTA DE LA SESSIÓ ORDINÀRIA DE LA JUNTA DE DISTRICTE", "Centre Municipal de Serveis Socials",
                 "Proposta de millora de l'enllumenat del carrer", "S'alça la sessió a les 20.15 hores."])


def correo_exportado() -> Caso:
    a, b = P[4], P[0]
    texto = f"""De: {a['nombre']} <{a['correo']}>
Para: Registro General <registro@sede.example.es>
Fecha: lunes, 9 de noviembre de 2026 10:42
Asunto: Subsanación del expediente de vado

Buenos días:

Adjunto la documentación que me pidieron para subsanar la solicitud de vado.
Mi vecina, {b['nombre']}, también firma la autorización (su teléfono es el {b['telefono']}).

Un saludo,
{a['nombre']}
{a['telefono']}
"""
    return Caso("correo_exportado", "txt", "utf-8-sig", "\r\n", texto,
                _persona(a, "nombre", "correo", "telefono") + _persona(b, "nombre", "telefono"),
                ["Registro General", "Subsanación del expediente de vado",
                 "Adjunto la documentación que me pidieron para subsanar la solicitud de vado."])


def listado_columnas_fijas() -> Caso:
    filas = "\n".join(f"{p['nombre']:<28}{p['dni']:<16}{p['telefono']:<18}" for p in P[:4])
    texto = f"""LISTADO DE BENEFICIARIOS - PROGRAMA DE RESPIRO FAMILIAR
Pág. 1

NOMBRE                      DNI             TELÉFONO
--------------------------------------------------------------
{filas}
--------------------------------------------------------------
Total beneficiarios: 4
"""
    datos = [d for p in P[:4] for d in _persona(p, "nombre", "dni", "telefono")]
    return Caso("listado_columnas_fijas", "txt", "cp850", "\r\n", texto, datos,
                ["LISTADO DE BENEFICIARIOS - PROGRAMA DE RESPIRO FAMILIAR", "Total beneficiarios: 4"])


def informe_unicode() -> Caso:
    p = P[2]
    texto = f"""INFORME SOCIAL

Se ha realizado visita domiciliaria a {p['nombre']}, nacida el {p['nacimiento']}, con
domicilio en {p['direccion']}.

La unidad familiar percibe una prestación que se abona en la cuenta {p['iban']}.
Se propone mantener la ayuda durante seis meses más.
"""
    return Caso("informe_unicode", "txt", "utf-16", "\r\n", texto,
                _persona(p, "nombre", "nacimiento", "direccion", "iban"),
                ["INFORME SOCIAL", "Se propone mantener la ayuda durante seis meses más."])


def horario_sin_datos() -> Caso:
    texto = """HORARIO DE ATENCIÓN AL PÚBLICO

Oficinas de Atención Ciudadana: de lunes a viernes, de 8.30 a 14.30 horas.
En julio y agosto el horario es de 9 a 14 horas.

Para pedir cita previa, acceda a la sede electrónica o llame al 010.
"""
    return Caso("horario_sin_datos", "txt", "cp1252", "\r\n", texto, [],
                ["HORARIO DE ATENCIÓN AL PÚBLICO"], sin_datos=True)


# --- Markdown ---------------------------------------------------------------------

def md_expediente() -> Caso:
    a, b = P[0], P[1]
    texto = f"""# Expediente 2026/LIC/0418

## Interesada

**Nombre:** {a['nombre']}
**DNI:** {a['dni']}
**Correo:** [{a['correo']}](mailto:{a['correo']})

## Otros interesados

| Nombre | DNI | Teléfono |
|:-------|:---:|---------:|
| {a['nombre']} | {a['dni']} | {a['telefono']} |
| {b['nombre']} | {b['dni']} | {b['telefono']} |

## Tramitación

1. Registro de entrada de la solicitud.
2. Requerimiento de documentación a _{b['nombre']}_.
3. Informe técnico favorable.

> Nota: la ficha completa está en <{a['correo']}> y en la [sede](https://sede.example.es/expedientes?dni={a['dni']}).

```
Contacto técnico: {b['correo']}
```
"""
    return Caso("md_expediente", "md", "utf-8", "\n", texto,
                _persona(a, "nombre", "dni", "correo", "telefono") + _persona(b, "nombre", "dni", "telefono", "correo"),
                ["## Interesada", "## Tramitación",
                 "1. Registro de entrada de la solicitud.", "3. Informe técnico favorable."])


def md_acta_va() -> Caso:
    texto = f"""# Acta de la Comissió de Festes

Data: 3 de novembre de 2026

## Assistents

- {P[2]['nombre']} (presidenta)
- {P[4]['nombre']}
- [{P[3]['nombre']}][fitxa]

## Acords

* S'aprova el pressupost de la cavalcada.
* {P[4]['nombre']} s'encarrega de la contractació de la banda.

[fitxa]: https://sede.example.es/fitxa?nom={P[3]['nombre'].replace(' ', '%20')}
"""
    return Caso("md_acta_va", "md", "cp1252", "\r\n", texto,
                [_d(P[2]["nombre"], "PERSON"), _d(P[4]["nombre"], "PERSON"), _d(P[3]["nombre"], "PERSON"),
                 _d(P[3]["nombre"].replace(" ", "%20"), "PERSON")],
                ["# Acta de la Comissió de Festes", "## Acords", "S'aprova el pressupost de la cavalcada."])


def md_html_e_imagenes() -> Caso:
    p = P[3]
    texto = f"""# Ficha de usuario del servicio de ayuda a domicilio

<div class="ficha">
<p>Usuaria: <b>{p['nombre']}</b></p>
<p>Teléfono de contacto: {p['telefono']}</p>
</div>

![Fotografía de {p['nombre']}](fotos/usuaria.jpg "Foto de {p['nombre']}")

Horas semanales asignadas: 6. Frecuencia: lunes, miércoles y viernes.
"""
    return Caso("md_html_e_imagenes", "md", "utf-8", "\n", texto,
                _persona(p, "nombre", "telefono"),
                ["# Ficha de usuario del servicio de ayuda a domicilio", '<div class="ficha">',
                 "Horas semanales asignadas: 6. Frecuencia: lunes, miércoles y viernes."])


def md_sin_datos() -> Caso:
    texto = """# Guía de tramitación de licencias

## Documentación

| Trámite | Plazo | Tasa |
|---|---|---|
| Obra menor | 1 mes | Sí |
| Primera ocupación | 3 meses | Sí |

- Consulte la [ordenanza](ordenanza.html) antes de presentar la solicitud.
- La solicitud se presenta en el *registro electrónico*.

```
Formato admitido: PDF firmado
```
"""
    return Caso("md_sin_datos", "md", "utf-8", "\n", texto, [],
                ["# Guía de tramitación de licencias"], sin_datos=True)


def corpus() -> list[Caso]:
    return [carta_notificacion(), solicitud_formulario(), acta_valenciano(), correo_exportado(),
            listado_columnas_fijas(), informe_unicode(), horario_sin_datos(),
            md_expediente(), md_acta_va(), md_html_e_imagenes(), md_sin_datos()]


def generar(destino: Path = DESTINO) -> list[dict]:
    destino.mkdir(parents=True, exist_ok=True)
    etiquetas = []
    for caso in corpus():
        (destino / f"{caso.nombre}.{caso.formato}").write_bytes(caso.bytes())
        e = asdict(caso)
        e.pop("texto")
        e["fichero"] = f"{caso.nombre}.{caso.formato}"
        e["codificacion"] = _BOM.get(caso.codificacion, caso.codificacion)
        e["bom"] = caso.codificacion in _BOM
        etiquetas.append(e)
    (destino / "etiquetas.json").write_text(json.dumps(etiquetas, ensure_ascii=False, indent=2), encoding="utf-8")
    return etiquetas


if __name__ == "__main__":
    generar(Path(sys.argv[1]) if len(sys.argv) > 1 else DESTINO)
    print("corpus de textos generado")
