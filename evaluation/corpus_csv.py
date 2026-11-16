"""Corpus de CSV con datos inventados y sus etiquetas.

Imita los CSV que exportan los programas de una administracion: listados de
personas con punto y coma (el separador de Excel en espanol) en Windows-1252,
con titulo encima de la cabecera, en valenciano, con columnas que no dicen
nada ("Col3"), con observaciones de texto libre (comillas dobladas y saltos de
linea dentro de un campo), una ficha en vertical, una cuenta repartida en
columnas, importes de personas, un presupuesto de obra (se sustituye: la
casilla viene marcada) y el mismo presupuesto con la casilla desmarcada (no
se toca), una estadistica sin personas, una exportacion separada por
tabuladores, otra por comas con todo entre comillas, y campos que empiezan
por "=" (texto: no se interpretan).

Etiqueta de cada fichero (etiquetas.json):
  datos         valores que no pueden quedar en el CSV de salida
  columnas      cabecera -> tipo esperado (None si no es personal: no puede cambiar)
  legitimos     texto que tiene que seguir tal cual
  codificacion  la del fichero (y la de la salida); bom: si lleva BOM
  sin_datos     el fichero tiene que salir identico, byte a byte
  desmarcados   tipos que se desmarcan para ese fichero

Uso: python -m evaluation.corpus_csv [DESTINO]   (por defecto evaluation/csv/corpus)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from evaluation.corpus_excel import _personas

DESTINO = Path("evaluation/csv/corpus")


def _fila(valores, sep=";", comillas=False) -> str:
    def campo(v):
        v = "" if v is None else str(v)
        if comillas or any(x in v for x in (sep, '"', "\n", "\r")):
            return '"' + v.replace('"', '""') + '"'
        return v
    return sep.join(campo(v) for v in valores)


def _csv(filas, sep=";", saltos="\r\n", comillas=False) -> str:
    return "".join(_fila(f, sep, comillas) + saltos for f in filas)


def _d(texto, tipo) -> dict:
    return {"text": str(texto), "type": tipo}


def _fecha(p) -> str:
    return p["nacimiento"].strftime("%d/%m/%Y")


def listado_solicitantes() -> dict:
    ps = _personas(10, 11)
    cab = ["Nº", "Nombre", "Apellidos", "DNI", "Fecha de nacimiento", "Teléfono", "Correo electrónico",
           "Distrito", "Estado"]
    distritos = ["Ciutat Vella", "Russafa", "Benimaclet", "Campanar", "Patraix"]
    filas = [cab] + [[i, p["nombre"], p["apellidos"], p["dni"], _fecha(p), p["telefono"], p["correo"],
                      distritos[i % 5], "Concedida" if i % 3 else "Pendiente"] for i, p in enumerate(ps, 1)]
    return {"nombre": "listado_solicitantes", "texto": _csv(filas), "codificacion": "cp1252",
            "datos": [_d(p[c], t) for p in ps for c, t in (("apellidos", "PERSON"), ("dni", "ES_DNI"),
                                                           ("telefono", "PHONE_NUMBER"),
                                                           ("correo", "EMAIL_ADDRESS"))]
            + [_d(_fecha(p), "DATE_OF_BIRTH") for p in ps],
            "columnas": {"Nº": None, "Nombre": "PERSON", "Apellidos": "PERSON", "DNI": "ES_DNI",
                         "Fecha de nacimiento": "DATE_OF_BIRTH", "Teléfono": "PHONE_NUMBER",
                         "Correo electrónico": "EMAIL_ADDRESS", "Distrito": None, "Estado": None},
            "legitimos": cab + distritos + ["Concedida", "Pendiente"]}


def inscritos_valenciano() -> dict:
    ps = _personas(8, 12)
    cab = ["Nom", "Cognoms", "DNI/NIE", "Telèfon", "Adreça electrònica", "Activitat"]
    activitats = ["Natació", "Ioga", "Escacs", "Teatre"]
    filas = [["Llistat d'inscrits als tallers municipals 2026"], [], cab] + [
        [p["nombre"], p["apellidos"], p["dni"], p["telefono"], p["correo"], activitats[i % 4]]
        for i, p in enumerate(ps)]
    return {"nombre": "inscritos_valenciano", "texto": _csv(filas), "codificacion": "utf-8", "bom": True,
            "datos": [_d(p[c], t) for p in ps for c, t in (("apellidos", "PERSON"), ("dni", "ES_DNI"),
                                                           ("telefono", "PHONE_NUMBER"),
                                                           ("correo", "EMAIL_ADDRESS"))],
            "columnas": {"Nom": "PERSON", "Cognoms": "PERSON", "DNI/NIE": "ES_DNI", "Telèfon": "PHONE_NUMBER",
                         "Adreça electrònica": "EMAIL_ADDRESS", "Activitat": None},
            "legitimos": ["Llistat d'inscrits als tallers municipals 2026"] + cab + activitats}


def sin_cabecera_clara() -> dict:
    ps = _personas(8, 13)
    cab = ["Col1", "Col2", "Col3", "Col4", "Col5"]
    filas = [cab] + [[i, p["completo"], p["dni"], p["correo"], "Alta"] for i, p in enumerate(ps, 1)]
    return {"nombre": "sin_cabecera_clara", "texto": _csv(filas, sep=",", saltos="\n"), "codificacion": "utf-8",
            "datos": [_d(p[c], t) for p in ps for c, t in (("completo", "PERSON"), ("dni", "ES_DNI"),
                                                           ("correo", "EMAIL_ADDRESS"))],
            "columnas": {"Col1": None, "Col2": "PERSON", "Col3": "ES_DNI", "Col4": "EMAIL_ADDRESS", "Col5": None},
            "legitimos": cab + ["Alta"]}


def con_observaciones() -> dict:
    ps = _personas(4, 14)
    cab = ["Expediente", "Fecha de entrada", "Observaciones"]
    obs = [
        f"Llamar a {ps[0]['completo']} al {ps[0]['telefono']}; dice \"urgente\".",
        f"Documentación completa.\nRevisado por el técnico el día 3.",
        f"El interesado pide que se notifique a {ps[1]['completo']}, con DNI {ps[1]['dni']}.",
        "Sin incidencias en la tramitación del expediente de vado.",
        "Pendiente de recibir el informe de la policía local.",
    ]
    filas = [cab] + [[f"2026/LIC/{100 + i}", f"0{i + 1}/03/2026", o] for i, o in enumerate(obs)]
    return {"nombre": "con_observaciones", "texto": _csv(filas), "codificacion": "cp1252",
            "datos": [_d(ps[0]["completo"], "PERSON"), _d(ps[0]["telefono"], "PHONE_NUMBER"),
                      _d(ps[1]["completo"], "PERSON"), _d(ps[1]["dni"], "ES_DNI")],
            # Fechas en una columna no personal: se sustituyen (casilla de FECHA marcada).
            "columnas": {"Fecha de entrada": "DATE_TIME"},
            "legitimos": cab + ["Sin incidencias en la tramitación del expediente de vado.",
                                "Pendiente de recibir el informe de la policía local.",
                                'dice ""urgente""', "Documentación completa.\nRevisado por el técnico el día 3."]}


def ficha_vertical() -> dict:
    ps = _personas(3, 15)
    filas = [["Nombre", *[p["completo"] for p in ps]], ["DNI", *[p["dni"] for p in ps]],
             ["Teléfono", *[p["telefono"] for p in ps]], ["Correo electrónico", *[p["correo"] for p in ps]]]
    return {"nombre": "ficha_vertical", "texto": _csv(filas), "codificacion": "utf-8",
            "datos": [_d(p[c], t) for p in ps for c, t in (("completo", "PERSON"), ("dni", "ES_DNI"),
                                                           ("telefono", "PHONE_NUMBER"),
                                                           ("correo", "EMAIL_ADDRESS"))],
            "legitimos": ["Nombre", "DNI", "Teléfono", "Correo electrónico"]}


def cuenta_repartida() -> dict:
    ps = _personas(5, 16)
    cab = ["Titular", "País", "Entidad", "Oficina", "DC", "Cuenta", "Concepto"]
    filas = [cab]
    datos = []
    for p in ps:
        iban = p["iban"].replace(" ", "")
        trozos = [iban[:4], iban[4:8], iban[8:12], iban[12:14], iban[14:]]
        filas.append([p["completo"], *trozos, "Ayuda comedor"])
        datos += [_d(p["completo"], "PERSON"), _d(trozos[4], "IBAN_CODE")]
    return {"nombre": "cuenta_repartida", "texto": _csv(filas), "codificacion": "cp1252", "datos": datos,
            "columnas": {"Titular": "PERSON", "País": "IBAN_CODE", "Entidad": "IBAN_CODE", "Oficina": "IBAN_CODE",
                         "DC": "IBAN_CODE", "Cuenta": "IBAN_CODE", "Concepto": None},
            "legitimos": cab + ["Ayuda comedor"]}


def ayudas_con_importes() -> dict:
    ps = _personas(6, 17)
    cab = ["Beneficiario", "DNI", "Importe concedido", "Línea de ayuda"]
    filas = [cab] + [[p["completo"], p["dni"], f"{p['importe']:.2f}".replace(".", ","), "Emergencia social"]
                     for p in ps]
    return {"nombre": "ayudas_con_importes", "texto": _csv(filas), "codificacion": "cp1252",
            "datos": [_d(p[c], t) for p in ps for c, t in (("completo", "PERSON"), ("dni", "ES_DNI"))],
            "columnas": {"Beneficiario": "PERSON", "DNI": "ES_DNI", "Importe concedido": "MONEY",
                         "Línea de ayuda": None},
            "legitimos": cab + ["Emergencia social"]}


_OBRA = [["Partida", "Descripción", "Unidad", "Medición", "Precio unitario", "Importe"],
         ["01.01", "Demolición de tabique de ladrillo", "m2", "12,50", "18,20", "227,50"],
         ["01.02", "Retirada de escombros a vertedero", "m3", "3,00", "45,00", "135,00"],
         ["02.01", "Enlucido de yeso en paramentos", "m2", "40,00", "12,10", "484,00"],
         ["02.02", "Pintura plástica lisa", "m2", "40,00", "6,35", "254,00"]]


def presupuesto_obra() -> dict:
    return {"nombre": "presupuesto_obra", "texto": _csv(_OBRA), "codificacion": "cp1252",
            "datos": [_d(v, "BUDGET_AMOUNT") for f in _OBRA[1:] for v in f[5:]],
            "columnas": {"Partida": None, "Descripción": None, "Unidad": None, "Medición": "BUDGET_AMOUNT",
                         "Precio unitario": "BUDGET_AMOUNT", "Importe": "BUDGET_AMOUNT"},
            "legitimos": _OBRA[0] + [f[1] for f in _OBRA[1:]]}


def presupuesto_desmarcado() -> dict:
    return {"nombre": "presupuesto_desmarcado", "texto": _csv(_OBRA), "codificacion": "cp1252",
            "datos": [], "desmarcados": ["BUDGET_AMOUNT"], "sin_datos": True,
            "legitimos": _OBRA[0]}


def estadistica_distritos() -> dict:
    filas = [["Distrito", "Habitantes", "Superficie (km²)", "Densidad"],
             ["Ciutat Vella", "27.000", "1,69", "15.976"], ["L'Eixample", "42.000", "1,73", "24.277"],
             ["Extramurs", "49.000", "1,97", "24.873"], ["Campanar", "38.000", "5,39", "7.050"]]
    return {"nombre": "estadistica_distritos", "texto": _csv(filas), "codificacion": "cp1252", "datos": [],
            "sin_datos": True, "legitimos": filas[0]}


def exportacion_tabuladores() -> dict:
    ps = _personas(6, 18)
    cab = ["Apellidos y nombre", "NIF", "Domicilio", "Fecha de alta"]
    filas = [cab] + [[f"{p['apellidos']}, {p['nombre']}", p["dni"], p["direccion"], "01/09/2025"] for p in ps]
    return {"nombre": "exportacion_tabuladores", "texto": _csv(filas, sep="\t"), "codificacion": "utf-8",
            "datos": [_d(f"{p['apellidos']}, {p['nombre']}", "PERSON") for p in ps]
            + [_d(p[c], t) for p in ps for c, t in (("dni", "ES_DNI"), ("direccion", "ADDRESS"))],
            "columnas": {"Apellidos y nombre": "PERSON", "NIF": "ES_DNI", "Domicilio": "ADDRESS",
                         "Fecha de alta": "DATE_TIME"},
            "legitimos": cab}


def comas_entre_comillas() -> dict:
    ps = _personas(6, 19)
    cab = ["Apellidos, Nombre", "Correo", "Teléfono móvil", "Servicio"]
    filas = [cab] + [[f"{p['apellidos']}, {p['nombre']}", p["correo"], p["telefono"], "Atención primaria"]
                     for p in ps]
    return {"nombre": "comas_entre_comillas", "texto": _csv(filas, sep=",", comillas=True), "codificacion": "utf-8",
            "datos": [_d(f"{p['apellidos']}, {p['nombre']}", "PERSON") for p in ps]
            + [_d(p[c], t) for p in ps for c, t in (("correo", "EMAIL_ADDRESS"), ("telefono", "PHONE_NUMBER"))],
            "columnas": {"Apellidos, Nombre": "PERSON", "Correo": "EMAIL_ADDRESS", "Teléfono móvil": "PHONE_NUMBER",
                         "Servicio": None},
            "legitimos": ['"Apellidos, Nombre"', '"Atención primaria"']}


def campos_con_igual() -> dict:
    ps = _personas(5, 20)
    cab = ["Nombre", "DNI", "Enlace", "Cálculo"]
    filas = [cab] + [[p["completo"], p["dni"], f'=HYPERLINK("https://sede.example.es/exp/{i}")', "=1+1"]
                     for i, p in enumerate(ps)]
    return {"nombre": "campos_con_igual", "texto": _csv(filas), "codificacion": "utf-8",
            "datos": [_d(p[c], t) for p in ps for c, t in (("completo", "PERSON"), ("dni", "ES_DNI"))],
            "columnas": {"Nombre": "PERSON", "DNI": "ES_DNI", "Cálculo": None},
            # La direccion web se sustituye (casilla de WEB marcada); la formula, como texto, se queda.
            "legitimos": ["=1+1", '"=HYPERLINK(""[WEB]"")"']}


def corpus() -> list[dict]:
    return [listado_solicitantes(), inscritos_valenciano(), sin_cabecera_clara(), con_observaciones(),
            ficha_vertical(), cuenta_repartida(), ayudas_con_importes(), presupuesto_obra(),
            presupuesto_desmarcado(), estadistica_distritos(), exportacion_tabuladores(),
            comas_entre_comillas(), campos_con_igual()]


def generar(destino: Path = DESTINO) -> list[dict]:
    destino.mkdir(parents=True, exist_ok=True)
    etiquetas = []
    for caso in corpus():
        texto = caso.pop("texto")
        bom = caso.get("bom", False)
        datos = (b"\xef\xbb\xbf" if bom else b"") + texto.encode(caso["codificacion"])
        caso["fichero"] = f"{caso['nombre']}.csv"
        caso.setdefault("bom", False)
        caso.setdefault("sin_datos", False)
        caso.setdefault("columnas", {})
        (destino / caso["fichero"]).write_bytes(datos)
        etiquetas.append(caso)
    (destino / "etiquetas.json").write_text(json.dumps(etiquetas, ensure_ascii=False, indent=2), encoding="utf-8")
    return etiquetas


if __name__ == "__main__":
    generar(Path(sys.argv[1]) if len(sys.argv) > 1 else DESTINO)
    print("corpus de CSV generado")
