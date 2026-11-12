"""Seudonimizacion reversible: etiquetas consistentes, tabla cifrada y reconstruccion.

Lo que se fija (datos inventados):
  - el mismo dato lleva siempre la misma etiqueta, numerada por tipo, aunque
    cambie de mayusculas, tildes o puntuacion; dos personas distintas no
    comparten etiqueta aunque compartan apellido;
  - la tabla va cifrada con la contrasena: sin ella no se abre, no lleva ningun
    valor en claro, y contrasena equivocada y fichero danado dan el mismo error;
  - ida y vuelta: reconstruir el documento seudonimizado sin tocar devuelve el
    original (byte a byte en texto y Markdown; el mismo texto en Word);
  - la reconstruccion tolera lo que un modelo de lenguaje hace con las
    etiquetas (mayusculas, corchetes, separador, traduccion) y avisa de lo que
    no puede resolver, sin adivinar.
"""
import json
from pathlib import Path

import docx
import pytest

from app.services.pii.analyzer import PiiAnalyzer
from app.services.seudonimo.etiquetas import Asignador
from app.services.seudonimo.reconstruir import reconstruir_fichero, reconstruir_texto
from app.services.seudonimo.tabla import (
    Cifrador,
    ContrasenaCortaError,
    TablaIlegibleError,
    abrir_tabla,
)
from app.services.texto.texto import anonimizar_texto
from tests.test_texto import CARTA, CORREO, DNI, MARKDOWN, NOMBRE, TELEFONO

CLAVE = "una clave larga de prueba"


@pytest.fixture(scope="module")
def analyzer():
    return PiiAnalyzer()


# --- etiquetas ---------------------------------------------------------------------

def test_el_mismo_dato_lleva_la_misma_etiqueta_numerada_por_tipo():
    a = Asignador()
    assert a.etiqueta("PERSON", "Teresa Navarro Gil") == "[PERSONA_1]"
    assert a.etiqueta("ES_DNI", "29.384.751-C") == "[DNI_1]"
    assert a.etiqueta("PERSON", "Vicent Ferrer Alandí") == "[PERSONA_2]"
    assert a.etiqueta("PERSON", "TERESA NAVARRO GIL") == "[PERSONA_1]"
    assert a.etiqueta("PERSON", "teresa  navarro gil") == "[PERSONA_1]"
    assert a.etiqueta("PERSON", "Vicent Ferrer Alandi") == "[PERSONA_2]"       # sin tilde
    assert a.etiqueta("ES_DNI", "29384751c") == "[DNI_1]"
    assert a.etiqueta("ES_DNI", "29384751-C") == "[DNI_1]"
    assert a.etiqueta("PHONE_NUMBER", "676 410 293") == "[TELEFONO_1]"
    assert a.etiqueta("PHONE_NUMBER", "+34 676410293") == "[TELEFONO_1]"
    assert a.etiqueta("EMAIL_ADDRESS", "Teresa.Navarro@Correo.es") == a.etiqueta("EMAIL_ADDRESS", CORREO)
    assert a.etiqueta("IBAN_CODE", "ES74 3159 0021 5720 0441 8836") == a.etiqueta(
        "IBAN_CODE", "es7431590021572004418836")


def test_dos_personas_con_el_mismo_apellido_no_se_unen():
    a = Asignador()
    una = a.etiqueta("PERSON", "María López Pérez")
    assert a.etiqueta("PERSON", "Sra. López") != una
    assert a.etiqueta("PERSON", "Juan López Pérez") != una
    assert a.etiqueta("PERSON", "López") != una


def test_la_forma_principal_es_la_mas_completa():
    a = Asignador()
    a.etiqueta("PERSON", "TERESA NAVARRO GIL")
    a.etiqueta("PERSON", "Teresa Navarro Gil")
    a.etiqueta("ES_DNI", "29384751C")
    a.etiqueta("ES_DNI", "29.384.751-C")
    tabla = a.tabla("txt", b"salida")
    assert tabla["etiquetas"]["DNI_1"]["principal"] == "29.384.751-C"
    assert tabla["etiquetas"]["PERSONA_1"]["variantes"] == ["TERESA NAVARRO GIL", "Teresa Navarro Gil"]


# --- tabla cifrada -------------------------------------------------------------------

def _tabla() -> dict:
    a = Asignador()
    a.etiqueta("PERSON", NOMBRE)
    a.etiqueta("ES_DNI", DNI)
    return a.tabla("txt", b"documento seudonimizado")


def test_la_tabla_cifrada_no_lleva_valores_y_se_abre_con_la_contrasena():
    cifrada = Cifrador(CLAVE).cifrar(_tabla())
    assert NOMBRE.encode() not in cifrada and DNI.encode() not in cifrada and b"Teresa" not in cifrada
    sobre = json.loads(cifrada)
    assert sobre["version"] == 1 and sobre["cifrado"] == "AES-256-GCM" and sobre["kdf"]["nombre"] == "scrypt"
    tabla = abrir_tabla(cifrada, CLAVE)
    assert tabla["etiquetas"]["PERSONA_1"]["principal"] == NOMBRE
    assert tabla["etiquetas"]["DNI_1"]["tipo"] == "ES_DNI"


def test_dos_cifrados_de_la_misma_tabla_son_distintos():
    assert Cifrador(CLAVE).cifrar(_tabla()) != Cifrador(CLAVE).cifrar(_tabla())


def test_contrasena_equivocada_y_tabla_danada_dan_el_mismo_error():
    cifrada = Cifrador(CLAVE).cifrar(_tabla())
    with pytest.raises(TablaIlegibleError) as mala:
        abrir_tabla(cifrada, "otra clave igual de larga")
    sobre = json.loads(cifrada)
    sobre["datos"] = sobre["datos"][:-8] + "AAAAAAA="
    with pytest.raises(TablaIlegibleError) as rota:
        abrir_tabla(json.dumps(sobre).encode(), CLAVE)
    with pytest.raises(TablaIlegibleError) as basura:
        abrir_tabla(b"esto no es una tabla", CLAVE)
    assert str(mala.value) == str(rota.value) == str(basura.value)


def test_una_contrasena_corta_se_rechaza():
    with pytest.raises(ContrasenaCortaError):
        Cifrador("corta")
    Cifrador("x" * 12)


# --- ida y vuelta ------------------------------------------------------------------------

def _seudonimizar(tmp_path: Path, analyzer, datos: bytes, formato: str):
    entrada, salida = tmp_path / f"e.{formato}", tmp_path / f"s.{formato}"
    entrada.write_bytes(datos)
    asignador = Asignador()
    informe = anonimizar_texto(entrada, salida, analyzer, "t", formato=formato, asignador=asignador)
    seudo = salida.read_bytes()
    return seudo, asignador.tabla(formato, seudo), informe


@pytest.mark.parametrize("codificacion,saltos", [("utf-8", "\n"), ("cp1252", "\r\n"), ("utf-16", "\r\n")])
def test_ida_y_vuelta_de_un_texto_byte_a_byte(tmp_path, analyzer, codificacion, saltos):
    original = CARTA.replace("\n", saltos).encode(codificacion)
    seudo, tabla, informe = _seudonimizar(tmp_path, analyzer, original, "txt")
    texto = seudo.decode(codificacion)
    for dato in (NOMBRE, DNI, TELEFONO, CORREO):
        assert dato.casefold() not in texto.casefold(), dato
    assert "[PERSONA_1]" in texto and "[DNI_1]" in texto and "[TELEFONO_1]" in texto and "[CORREO_1]" in texto
    assert texto.count("[PERSONA_1]") == 2          # el nombre en mayusculas es la misma persona
    assert informe.treatment == "pseudonymize"
    r = reconstruir_fichero(seudo, "txt", tabla)
    assert r.exacta and r.datos == original
    assert not r.no_resueltas and not r.alteradas


def test_ida_y_vuelta_de_un_markdown_byte_a_byte(tmp_path, analyzer):
    original = MARKDOWN.encode("utf-8")
    seudo, tabla, _ = _seudonimizar(tmp_path, analyzer, original, "md")
    texto = seudo.decode("utf-8")
    assert NOMBRE not in texto and CORREO not in texto
    assert "_[PERSONA_1]_" in texto and "| [PERSONA_1] | [DNI_1] | [TELEFONO_1] |" in texto
    assert "(mailto:%5BCORREO_1%5D)" in texto
    r = reconstruir_fichero(seudo, "md", tabla)
    assert r.exacta and r.datos == original


def test_ida_y_vuelta_de_un_word(tmp_path, analyzer):
    from app.services.office.docx import anonimizar_docx
    from evaluation.corpus_word import PERSONAS, carta

    entrada, salida = tmp_path / "e.docx", tmp_path / "s.docx"
    carta(entrada, PERSONAS[0])
    asignador = Asignador()
    anonimizar_docx(entrada, salida, analyzer, lambda e, s, f: None, "t", asignador=asignador)
    seudo = salida.read_bytes()
    visible = "\n".join(p.text for p in docx.Document(salida).paragraphs)
    assert PERSONAS[0]["nombre"] not in visible and "[PERSONA_1]" in visible
    r = reconstruir_fichero(seudo, "docx", asignador.tabla("docx", seudo))
    vuelta = tmp_path / "v.docx"
    vuelta.write_bytes(r.datos)
    assert r.exacta
    assert [p.text for p in docx.Document(vuelta).paragraphs] == [p.text for p in docx.Document(entrada).paragraphs]


# --- reconstruccion tolerante ------------------------------------------------------------------

@pytest.fixture(scope="module")
def tabla():
    a = Asignador()
    a.etiqueta("PERSON", "Teresa Navarro Gil")
    a.etiqueta("PERSON", "Vicent Ferrer Alandí")
    a.etiqueta("ES_DNI", "29.384.751-C")
    a.etiqueta("PHONE_NUMBER", "676 410 293")
    a.etiqueta("PHONE_NUMBER", "963 528 104")
    a.etiqueta("EMAIL_ADDRESS", "teresa.navarro@correo.es")
    a.etiqueta("ADDRESS", "Calle de Ruzafa 18, 46004 Valencia")
    return a.tabla("txt", b"x")


@pytest.mark.parametrize("alterado,esperado", [
    ("Resumen: [PERSONA_1] pide cita.", "Resumen: Teresa Navarro Gil pide cita."),
    ("[Persona_1] y [persona_2]", "Teresa Navarro Gil y Vicent Ferrer Alandí"),
    ("Llamar a PERSONA_1 al TELEFONO_2.", "Llamar a Teresa Navarro Gil al 963 528 104."),
    ("{PERSONA_1} <DNI_1> \\[PERSONA\\_2\\]", "Teresa Navarro Gil 29.384.751-C Vicent Ferrer Alandí"),
    ("**PERSONA_1** (PERSONA_2)", "**Teresa Navarro Gil** (Vicent Ferrer Alandí)"),
    ("[PERSONA 1], [PERSONA-2], [PERSONA1]", "Teresa Navarro Gil, Vicent Ferrer Alandí, Teresa Navarro Gil"),
    ("[PERSON_1] [PHONE_2] [TELÈFON_1] [EMAIL_1] [ADDRESS_1] [NOMBRE_2]",
     "Teresa Navarro Gil 963 528 104 676 410 293 teresa.navarro@correo.es "
     "Calle de Ruzafa 18, 46004 Valencia Vicent Ferrer Alandí"),
    ("[PERSONA_1]'s request; las de [PERSONA_2]s", "Teresa Navarro Gil's request; las de Vicent Ferrer Alandís"),
    ("[TELÉFONO_1]", "676 410 293"),
])
def test_lo_que_un_modelo_hace_con_las_etiquetas_se_resuelve(tabla, alterado, esperado):
    r = reconstruir_texto(alterado, tabla)
    assert r.texto == esperado
    assert not r.no_resueltas


def test_las_formas_alteradas_se_listan_para_revisar_y_las_exactas_no(tabla):
    r = reconstruir_texto("[PERSONA_1] y [Person 2] y PERSONA_1", tabla)
    assert r.resueltas == 3
    assert sorted(a["forma"] for a in r.alteradas) == ["PERSONA_1", "[Person 2]"]
    assert {a["etiqueta"] for a in r.alteradas} == {"PERSONA_1", "PERSONA_2"}


@pytest.mark.parametrize("texto,forma", [
    ("Habla con [PERSONA_9].", "[PERSONA_9]"),
    ("El [PERSONA_DNI_1] no existe.", "[PERSONA_DNI_1]"),
    ("Y [VEHICULO_1] tampoco.", "[VEHICULO_1]"),
])
def test_una_etiqueta_inventada_se_deja_y_se_avisa(tabla, texto, forma):
    r = reconstruir_texto(texto, tabla)
    assert r.texto == texto
    assert r.no_resueltas == [forma]


def test_el_texto_normal_no_se_toca(tabla):
    texto = "La persona 1 de la lista llamó al teléfono 2 veces. Ver DNI 1 vez. Persona-1? no: persona1."
    r = reconstruir_texto(texto, tabla)
    assert r.texto.startswith("La persona 1 de la lista llamó al teléfono 2 veces. Ver DNI 1 vez.")


def test_avisa_de_las_etiquetas_de_la_tabla_que_no_aparecen(tabla):
    r = reconstruir_texto("Solo [PERSONA_1].", tabla)
    assert "PERSONA_2" in r.no_usadas and "PERSONA_1" not in r.no_usadas


def test_el_informe_de_la_reconstruccion_no_lleva_valores(tabla):
    r = reconstruir_texto("[PERSONA_1] [Person 2] [PERSONA_9]", tabla)
    resumen = json.dumps(r.resumen(), ensure_ascii=False)
    assert "Teresa" not in resumen and "Vicent" not in resumen


def test_la_segunda_opinion_no_toma_las_etiquetas_por_datos(tmp_path, analyzer):
    """"Domicilio: [DIRECCION_1]" se leia como una direccion sin sustituir."""
    texto = "Destinataria: Teresa Navarro Gil\nDomicilio: Calle de Ruzafa 18, 2º A, 46004 Valencia\n"
    _, _, informe = _seudonimizar(tmp_path, analyzer, texto.encode("utf-8"), "txt")
    assert informe.residual_entities == 0 and not informe.manual_review_required
