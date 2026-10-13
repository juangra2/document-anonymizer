"""Banco de pruebas de telefonos y direcciones: que formatos se recogen y cuales no.

Fabrica un documento con muchos formatos reales —version digital y escaneada,
porque el OCR cambia los formatos— y, con --medir, lo pasa por el pipeline y
dice por cada caso si se detecta en digital, si se detecta en escaneado y si
queda tapado en el PDF entregado.  Incluye casos trampa (expedientes, importes,
codigos postales, texto que parece direccion) para medir falsos positivos.

Solo diagnostico.  Datos inventados.

    python scripts/banco_contacto.py               # fabrica samples/banco/
    python scripts/banco_contacto.py --medir       # y mide (usa la GPU)
"""
from __future__ import annotations

import argparse
import io
import json
import random
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import fitz
from PIL import Image, ImageFilter

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
SALIDA = RAIZ / "samples" / "banco"


@dataclass
class Caso:
    id: str
    tipo: str          # PHONE_NUMBER / ADDRESS / FP_TELEFONO / FP_DIRECCION
    formato: str       # que se prueba, en palabras
    texto: str         # la linea (o lineas) tal como va en el documento
    valor: str         # lo que hay que tachar (o, en las trampas, lo que NO)


def _numeros():
    """Numeros distintos para cada caso: que uno no case con el de otro."""
    rng = random.Random(7)
    vistos = set()
    while True:
        movil = f"6{rng.randint(10_000_000, 99_999_999)}"
        if movil not in vistos:
            vistos.add(movil)
            yield movil


def _agrupa(n: str, grupos: tuple[int, ...], sep: str) -> str:
    partes, i = [], 0
    for g in grupos:
        partes.append(n[i:i + g]); i += g
    return sep.join(partes)


def casos() -> list[Caso]:
    gen = _numeros()
    out: list[Caso] = []

    def nuevo(prefijo: str) -> str:
        return prefijo + next(gen)[len(prefijo):]

    # --- telefonos: tipo x agrupacion x separador ---------------------------
    for tipo, pref in (("movil 6xx", "6"), ("movil 7xx", "7"), ("fijo 96x", "96")):
        for grupos, gnombre in (((3, 3, 3), "3-3-3"), ((3, 2, 2, 2), "3-2-2-2")):
            for sep, snombre in ((" ", "espacios"), (".", "puntos"), ("-", "guiones")):
                n = _agrupa(nuevo(pref), grupos, sep)
                out.append(Caso("", "PHONE_NUMBER", f"{tipo}, {gnombre}, {snombre}",
                                f"Datos facilitados: {n}", n))
        n = nuevo(pref)
        out.append(Caso("", "PHONE_NUMBER", f"{tipo}, sin separar", f"Datos facilitados: {n}", n))
    # prefijo internacional
    for p, pnombre in (("+34 ", "+34 con espacio"), ("+34", "+34 pegado"), ("0034 ", "0034"),
                       ("(+34) ", "(+34)")):
        for pref, tipo in (("6", "movil"), ("96", "fijo")):
            n = p + _agrupa(nuevo(pref), (3, 3, 3), " ")
            out.append(Caso("", "PHONE_NUMBER", f"{pnombre}, {tipo}", f"Llame al {n}", n))
    # etiquetas
    for et in ("Tel.", "Tfno.", "Teléfono:", "Telèfon:", "Móvil:", "Mòbil:", "Tel:", "T."):
        n = _agrupa(nuevo("6"), (3, 3, 3), " ")
        out.append(Caso("", "PHONE_NUMBER", f"etiqueta '{et}'", f"{et} {n}", n))
    n = _agrupa(nuevo("6"), (3, 3, 3), " ")
    out.append(Caso("", "PHONE_NUMBER", "sin etiqueta, suelto", n, n))
    # extension, varios, partido, prosa, formulario
    n = _agrupa(nuevo("96"), (3, 2, 2, 2), " ")
    out.append(Caso("", "PHONE_NUMBER", "fijo con extension 'ext.'", f"Tel. {n} ext. 214", n))
    n = _agrupa(nuevo("96"), (3, 3, 3), " ")
    out.append(Caso("", "PHONE_NUMBER", "fijo con extension 'extensión'", f"Teléfono {n}, extensión 31", n))
    a, b = _agrupa(nuevo("6"), (3, 3, 3), " "), _agrupa(nuevo("96"), (3, 2, 2, 2), " ")
    out.append(Caso("", "PHONE_NUMBER", "dos en la misma linea con '/' (primero)", f"Tel. {a} / {b}", a))
    out.append(Caso("", "PHONE_NUMBER", "dos en la misma linea con '/' (segundo)", f"Tel. {a} / {b}", b))
    a, b = nuevo("6"), nuevo("7")
    out.append(Caso("", "PHONE_NUMBER", "dos sin separar con ' - ' (segundo)", f"Móvil {a} - {b}", b))
    n = nuevo("6")
    out.append(Caso("", "PHONE_NUMBER", "partido por un salto de linea",
                    f"puede llamar al teléfono {n[:3]} {n[3:5]}\n{n[5:7]} {n[7:]} en horario de mañana",
                    f"{n[:3]} {n[3:5]} {n[5:7]} {n[7:]}"))
    n = _agrupa(nuevo("6"), (3, 3, 3), " ")
    out.append(Caso("", "PHONE_NUMBER", "dentro de prosa",
                    f"La persona interesada indica que puede ser localizada en el {n} durante la tarde.", n))
    n = _agrupa(nuevo("96"), (3, 2, 2, 2), " ")
    out.append(Caso("", "PHONE_NUMBER", "prosa en valenciano",
                    f"La persona interessada pot ser localitzada al número {n} de vesprada.", n))
    n = _agrupa(nuevo("6"), (3, 2, 2, 2), " ")
    out.append(Caso("", "PHONE_NUMBER", "campo de formulario", f"Teléfono de contacto: ............ {n}", n))

    # etiqueta en una linea y el valor en la siguiente (lectura de barras de c2)
    n = _agrupa(nuevo("6"), (3, 3, 3), " ")
    out.append(Caso("", "PHONE_NUMBER", "etiqueta 'Tfno.:' y numero en la linea siguiente",
                    f"Tfno.:\n+34 {n}", f"+34 {n}"))
    n = nuevo("6")
    out.append(Caso("", "PHONE_NUMBER", "nomina: telefono tras el centro de trabajo",
                    f"Centro de trabajo\nUniversitat Mediterrània d'Elx\nTfno.:\n{n}", n))

    # --- trampas de telefono: NO son telefonos --------------------------------
    for texto, valor, fmt in (
        ("Expediente nº 2023/612345678", "612345678", "expediente con 9 cifras"),
        ("Importe total: 612.345,67 euros", "612.345,67", "importe"),
        ("Importe: 961 234 euros", "961 234", "importe con espacio"),
        ("Código postal 46001 Valencia", "46001", "codigo postal"),
        ("Referencia catastral 9634567YJ2793S0001AB", "9634567", "referencia catastral"),
        ("Registro de entrada núm. 963456781 de 12/03/2024", "963456781", "registro de entrada (9 cifras)"),
        ("CCC 2100 0418 45 0200051332", "2100 0418 45", "cuenta CCC"),
        ("Fecha 06/12/2024, hora 10:30", "06/12/2024", "fecha"),
        ("Superficie 612 m2 y 345 m2 de terraza", "612", "superficies"),
    ):
        out.append(Caso("", "FP_TELEFONO", fmt, texto, valor))

    # --- direcciones ------------------------------------------------------------
    vias = [("C/", "C/ Major"), ("C/.", "C/. Major"), ("Calle", "Calle Mayor"), ("Carrer", "Carrer de la Pau"),
            ("Avda.", "Avda. del Puerto"), ("Avda", "Avda del Puerto"), ("Av.", "Av. Blasco Ibáñez"),
            ("Avinguda", "Avinguda del Port"), ("Pza.", "Pza. de la Reina"), ("Pza", "Pza de la Reina"),
            ("Plaça", "Plaça de la Mare de Déu"), ("Pg.", "Pg. de l'Albereda"), ("Passeig", "Passeig de l'Albereda"),
            ("Ctra.", "Ctra. de Barcelona"), ("Ctra", "Ctra de Barcelona"), ("Camí", "Camí de Vera"),
            ("Partida", "Partida de la Torre")]
    num = iter(range(11, 999, 7))
    # Una calle distinta por caso: si dos casos comparten calle y solo cambia el
    # numero, detectar uno parece detectar el otro.
    calles = iter(["Literato Azorín", "Sueca", "Cuba", "Denia", "Cádiz", "Burriana", "Puerto Rico",
                   "Doctor Sumsi", "Matías Perelló", "Pintor Salvador Abril", "Carlos Cervera",
                   "Maestro Gozalbo", "Conde de Altea", "Císcar", "Salamanca", "Jorge Juan",
                   "Ciscar", "Pizarro", "Hernán Cortés", "Martínez Cubells", "Barcas", "Moratín",
                   "Ribera", "Xàtiva", "Colón", "Sorní", "Grabador Esteve", "Joaquín Costa",
                   "Almirante Cadarso", "Isabel la Católica", "Félix Pizcueta", "Játiva"])
    for nombre, via in vias:
        d = f"{via}, {next(num)}, 46022 València"
        out.append(Caso("", "ADDRESS", f"via '{nombre}', con etiqueta", f"Domicilio: {d}", d))
        d = f"{via} {next(calles).split()[0]}, {next(num)}, 46022 València"
        out.append(Caso("", "ADDRESS", f"via '{nombre}', sin etiqueta", d, d))
    for fmt, d in (("numero 'nº'", f"Calle Colón nº {next(num)}, 46004 Valencia"),
                   ("numero 'núm.'", f"Carrer de Russafa, núm. {next(num)}, 46006 València"),
                   ("sin numero 's/n'", "Avda. de la Constitución s/n, 46009 Valencia"),
                   ("numero sin coma", f"Calle Sueca {next(num)} 46006 Valencia")):
        out.append(Caso("", "ADDRESS", fmt, f"Domicilio: {d}", d))
    for piso in ("3º B", "2ª", "pta. 4", "esc. A, 5º 9ª", "bajo", "entresuelo"):
        d = f"Calle de {next(calles)}, {next(num)}, {piso}, 46006 Valencia"
        out.append(Caso("", "ADDRESS", f"piso/puerta '{piso}'", f"Domicilio: {d}", d))
    d = f"Calle de {next(calles)}, {next(num)}, 4º C"
    out.append(Caso("", "ADDRESS", "sin CP ni municipio", f"Domicilio: {d}", d))
    d = f"Calle de {next(calles)}, {next(num)}"
    out.append(Caso("", "ADDRESS", "solo via y numero", f"Domicilio: {d}", d))
    n = next(num)
    out.append(Caso("", "ADDRESS", "repartida en tres lineas",
                    f"Calle del Doctor Sumsi, {n}, 2º\n46005 Valencia\n(Valencia)",
                    f"Calle del Doctor Sumsi, {n}, 2º 46005 Valencia"))
    d = f"calle Ruzafa número {next(num)}, piso tercero"
    out.append(Caso("", "ADDRESS", "en prosa, castellano",
                    f"El interesado reside en la {d}, de esta ciudad, según consta en el padrón.", d))
    d = f"carrer de Cuba, número {next(num)}, porta 7"
    out.append(Caso("", "ADDRESS", "en prosa, valenciano",
                    f"La persona interessada té el domicili en el {d}, de València, segons el padró.", d))
    for et in ("Domicilio:", "Dirección:", "Adreça:", "Domicili:", "Dom.:"):
        d = f"Calle {next(calles)}, {next(num)}, 46006 Valencia"
        out.append(Caso("", "ADDRESS", f"campo etiquetado '{et}'", f"{et} {d}", d))

    # etiqueta en una linea y el valor en la siguiente (lectura de barras de c2)
    d = f"Paseo de {next(calles)}, {next(num)}, 2º B, 18001 Granada"
    out.append(Caso("", "ADDRESS", "etiqueta 'Domicilio fiscal:' y valor en la linea siguiente",
                    f"Domicilio fiscal:\n{d}", d))
    d = f"Calle de {next(calles)}, {next(num)}, 4º A, 03003 Alicante"
    out.append(Caso("", "ADDRESS", "nomina: domicilio tras el centro de trabajo",
                    f"Centro de trabajo\nUniversitat Mediterrània d'Elx\nAntigüedad\n12 de febrer de 2015\n"
                    f"Domicilio fiscal:\n{d}", d))

    # --- trampas de direccion: NO son direcciones -------------------------------
    for texto, valor, fmt in (
        ("La calle estaba cortada por obras durante la mañana.", "calle estaba cortada", "'calle' en prosa"),
        ("Se celebró en la Plaza de Toros de Valencia.", "Plaza de Toros", "lugar publico"),
        ("Recorrió el Camino de Santiago en 2019.", "Camino de Santiago", "nombre propio con 'Camino'"),
        ("Avenida de la Constitución es una película de 1950.", "Avenida de la Constitución", "titulo"),
        ("Partida presupuestaria 2024/4410/22699", "Partida presupuestaria", "'Partida' presupuestaria"),
        ("La calle Mayor está cortada por obras desde el lunes.", "calle Mayor", "'calle Mayor' en prosa"),
        ("Recorrió el Camino de Santiago, etapa número 12.", "Camino de Santiago", "'Camino' + 'número'"),
        ("El Paseo de Gracia, 2023, fue el lugar elegido.", "Paseo de Gracia", "via + año"),
        ("Según la avenida número 4 del plan urbanístico", "avenida número 4", "'avenida número' sin nombre"),
        ("Para el detalle, ver Pg. 12 del informe adjunto.", "Pg. 12", "'Pg.' de pagina"),
        ("como se indica en la Pg. 46, 46002 ejemplares impresos", "Pg. 46", "'Pg.' de pagina + cifra"),
        ("Partida 3 del presupuesto: 46100 euros", "Partida 3", "'Partida' + cifra"),
    ):
        out.append(Caso("", "FP_DIRECCION", fmt, texto, valor))

    # --- carta: membrete y destinatario en la franja de CABECERA -----------------
    # El membrete del ayuntamiento no se tacha; el destinatario si.
    for tipo, fmt, valor in (
        ("FP_TELEFONO", "carta: telefono del membrete", "96 170 05 00"),
        ("FP_DIRECCION", "carta: direccion del membrete", "Plaça Major, 1, 46410 Sueca"),
        ("ADDRESS", "carta: direccion del destinatario", "C/ Sant Josep, 14, 46410 Sueca"),
        ("PHONE_NUMBER", "carta: telefono del destinatario", "655 214 873"),
    ):
        out.append(Caso("", tipo, fmt, "(en la carta de la primera pagina)", valor))

    for i, c in enumerate(out, 1):
        c.id = f"{'T' if 'TELEF' in c.tipo or c.tipo == 'PHONE_NUMBER' else 'D'}{i:03d}"
    return out


# La carta de la primera pagina: todo en el 12 % superior (< 101 pt), que es donde
# la zona de cabecera excluia antes el telefono y la direccion.
_CARTA = ["Ajuntament de Sueca", "Plaça Major, 1, 46410 Sueca", "Tel. 96 170 05 00",
          "Sra. Xelo Brufau Ivars", "C/ Sant Josep, 14, 46410 Sueca", "Tel. 655 214 873"]


def fabricar_digital(lista: list[Caso], destino: Path) -> None:
    doc = fitz.open()
    carta = doc.new_page(width=595, height=842)
    y = 25
    for linea in _CARTA:
        carta.insert_text((50, y), linea, fontsize=8, fontname="helv"); y += 11
    y = 300
    for linea in ("Por la presente se le notifica la resolucion del expediente de",
                  "referencia, que se adjunta para su conocimiento y efectos."):
        carta.insert_text((50, y), linea, fontsize=10, fontname="helv"); y += 14
    pagina = None
    y = 0
    for c in lista:
        if c.formato.startswith("carta:"):
            continue
        lineas = [f"Caso {c.id}"] + c.texto.split("\n")
        alto = 15 * len(lineas) + 10
        # Los casos van entre las franjas de cabecera y pie (12 % arriba y abajo,
        # segmenters._HEADER_RATIO): ahi el telefono y la direccion no se buscan
        # por diseño, y el banco mide el formato, no la posicion.
        if pagina is None or y + alto > 725:
            pagina = doc.new_page(width=595, height=842)
            # Titulo neutro: un nombre de institucion arriba haria de membrete y el
            # filtro institucional apartaria los primeros casos de cada pagina.
            pagina.insert_text((50, 50), "Banco de pruebas de datos de contacto", fontsize=9)
            y = 120
        for linea in lineas:
            pagina.insert_text((50, y), linea, fontsize=10, fontname="helv")
            y += 15
        y += 10
    doc.save(destino)


def fabricar_escaneado(origen: Path, destino: Path, dpi: int = 200) -> None:
    """Como un escaneo: a imagen, gris, algo de desenfoque y ruido, un poco girada."""
    rng = random.Random(3)
    src = fitz.open(origen)
    out = fitz.open()
    for p in src:
        pix = p.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
        img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
        img = img.rotate(rng.uniform(-0.6, 0.6), fillcolor=255, resample=Image.BICUBIC)
        img = img.filter(ImageFilter.GaussianBlur(0.6))
        ruido = Image.effect_noise(img.size, 12)
        img = Image.blend(img, ruido.point(lambda v: 255 if v > 40 else v), 0.06)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=70)
        pagina = out.new_page(width=p.rect.width, height=p.rect.height)
        pagina.insert_image(pagina.rect, stream=buf.getvalue())
    out.save(destino)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--medir", action="store_true")
    args = ap.parse_args()
    SALIDA.mkdir(parents=True, exist_ok=True)
    lista = casos()
    (SALIDA / "casos.json").write_text(json.dumps([asdict(c) for c in lista], ensure_ascii=False,
                                                  indent=1), encoding="utf-8")
    fabricar_digital(lista, SALIDA / "banco_digital.pdf")
    fabricar_escaneado(SALIDA / "banco_digital.pdf", SALIDA / "banco_escaneado.pdf")
    print(f"{len(lista)} casos -> {SALIDA}")
    if args.medir:
        from scripts.banco_contacto_medir import medir
        medir(lista, SALIDA)


if __name__ == "__main__":
    main()
