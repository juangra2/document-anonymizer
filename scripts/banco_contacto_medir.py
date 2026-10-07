"""Mide el banco de telefonos y direcciones (ver banco_contacto.py).

Por cada caso y version (digital, escaneado):
  detectado   si el pipeline lo detecta con su tipo: "si", "parcial NN %" (una
              direccion cubierta solo en parte) o "no"; "otro tipo" si lo tacha
              con otro tipo
  legible     si el valor se sigue leyendo en el PDF entregado (lectura de
              barras: OCR del PDF de salida, con erratas toleradas)
En las trampas, "detectado" es si se tomo por telefono/direccion (falso positivo).
"""
from __future__ import annotations

import difflib
import json
import re
from pathlib import Path


def _cifras(s: str) -> str:
    return re.sub(r"\D", "", s)


def _cobertura(valor: str, textos: list[str]) -> float:
    """Fraccion de los caracteres de `valor` que cubre la mejor deteccion."""
    from evaluation.lectura_salida import _aplanar

    v = _aplanar(valor)
    mejor = 0.0
    for t in textos:
        m = difflib.SequenceMatcher(None, v, _aplanar(t), autojunk=False)
        mejor = max(mejor, sum(b.size for b in m.get_matching_blocks()) / max(1, len(v)))
    return mejor


def _estado(caso: dict, entidades: list[tuple[str, str]]) -> str:
    tipo = caso["tipo"]
    if tipo in ("PHONE_NUMBER", "FP_TELEFONO"):
        buscado = "PHONE_NUMBER"
        if tipo == "FP_TELEFONO":
            linea = _cifras(caso["texto"]) or _cifras(caso["valor"])
            hits = [t for ty, t in entidades if ty == buscado and _cifras(t) and _cifras(t) in linea
                    and _cifras(caso["valor"])[:3] in _cifras(t)]
            return "SI (falso positivo)" if hits else "no"
        cif = _cifras(caso["valor"])
        if any(ty == buscado and cif in _cifras(t) for ty, t in entidades):
            return "si"
        # Con prefijo internacional: tachado el numero, pero no el prefijo.
        for pref in ("0034", "34"):
            if cif.startswith(pref) and any(ty == buscado and cif[len(pref):] in _cifras(t)
                                            for ty, t in entidades):
                return "sin prefijo"
        if any(cif and cif in _cifras(t) for _, t in entidades):
            return "otro tipo"
        return "no"
    # direcciones
    from evaluation.lectura_salida import _aplanar

    if tipo == "FP_DIRECCION":
        linea = _aplanar(caso["valor"] if caso["texto"].startswith("(en la carta") else caso["texto"])
        hits = [t for ty, t in entidades if ty == "ADDRESS" and _aplanar(t) and _aplanar(t) in linea]
        return "SI (falso positivo)" if hits else "no"
    # Solo detecciones con el MISMO numero de portal: otra direccion de la misma
    # calle no cuenta.
    num = re.findall(r"\b\d{1,3}\b", caso["valor"])
    propias = [t for ty, t in entidades if ty == "ADDRESS"
               and (not num or num[0] in re.findall(r"\b\d{1,3}\b", t))]
    cob = _cobertura(caso["valor"], propias)
    if cob >= 0.9:
        return "si"
    if cob >= 0.3:
        return f"parcial {round(100 * cob)} %"
    otra = _cobertura(caso["valor"], [t for _, t in entidades])
    return "otro tipo" if otra >= 0.6 else "no"


def medir(lista, carpeta: Path) -> None:
    from evaluation.lectura_salida import _aplanar, aparece, leer_documento
    from app.services.pdf.pipeline import PdfAnonymizationPipeline

    pipe = PdfAnonymizationPipeline()
    casos = [c if isinstance(c, dict) else c.__dict__ for c in lista]
    resultado = {c["id"]: {"caso": c} for c in casos}
    for version in ("digital", "escaneado"):
        entrada = carpeta / f"banco_{version}.pdf"
        salida = carpeta / f"banco_{version}_ANONIMIZADO.pdf"
        rep = pipe.process(input_path=entrada, output_path=salida, job_id=f"banco_{version}",
                           mode="strict", collect_entities=True)
        entidades = [(e.type, e.text) for e in rep.detected_entities]
        leido_entrada = _aplanar(" ".join(l.texto for l in leer_documento(entrada)))
        leido_salida = _aplanar(" ".join(l.texto for l in leer_documento(salida)))
        for c in casos:
            v = _aplanar(c["valor"])
            if not aparece(v, leido_entrada):
                legible = "no se leia ni en la entrada"
            else:
                legible = "LEGIBLE" if aparece(v, leido_salida) else "tapado"
            resultado[c["id"]][version] = {"detectado": _estado(c, entidades), "legible": legible}
        print(f"{version}: {len(entidades)} entidades, camino {rep.document_type}", flush=True)
    (carpeta / "resultado.json").write_text(json.dumps(resultado, ensure_ascii=False, indent=1),
                                            encoding="utf-8")
    print(f"\n{'caso':6s} {'formato':48s} {'digital':22s} {'escaneado':22s}")
    for r in resultado.values():
        c = r["caso"]
        d, e = r["digital"], r["escaneado"]
        print(f"{c['id']:6s} {c['formato'][:48]:48s} {d['detectado'][:11]:11s}/{d['legible'][:10]:10s} "
              f"{e['detectado'][:11]:11s}/{e['legible'][:10]:10s}")
