"""El informe final se genera desde los resultados bajo seguimiento y es autocontenido:
el servidor del ayuntamiento puede no tener red."""
import re

from evaluation.informe_final import RAIZ, generar


def test_el_informe_se_genera_completo_y_sin_nada_de_internet(tmp_path):
    salida = tmp_path / "informe.html"
    generar(RAIZ / "evaluation/reference/c4", salida)
    html = salida.read_text(encoding="utf-8")
    for seccion in ("Cómo leer las cifras", "Resultados por tipo de documento", "Media de los tres",
                    "Evolución", "F1 por tipo de dato", "Lectura de barras",
                    "Banco de teléfonos y direcciones", "Casos difíciles",
                    "Metadatos y contenido oculto", "Limitaciones conocidas"):
        assert seccion in html, seccion
    assert not re.search(r"""(src|href)=["']?(https?:)?//""", html), "nada se carga de fuera"
    assert "@import" not in html and "<script" not in html
