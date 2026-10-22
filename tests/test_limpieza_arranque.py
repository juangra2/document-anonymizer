"""La limpieza de arranque no le borra el trabajo a un proceso vivo.

Un segundo servicio que ni siquiera llega a ocupar el puerto ejecuta su
limpieza de arranque; si borrara la carpeta del documento que el servicio vivo
esta procesando, saltaria un FileNotFoundError a media pagina y el usuario
recibiria un error interno sin causa entendible.

Pasa sin reinicios mal hechos: cuando un gestor de procesos reinicia con trabajo
en curso, cuando dos instancias se levantan a la vez aunque una falle, y cuando
un arranque se solapa con el apagado anterior.
"""
import time
from pathlib import Path

import pytest

from app.services.jobs.store import JobStore
from app.services.storage import propiedad


@pytest.fixture
def raiz(tmp_path, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "temp_root", str(tmp_path))
    return tmp_path


def _carpeta(raiz: Path, nombre: str, testigo: str | None) -> Path:
    carpeta = raiz / nombre
    carpeta.mkdir()
    (carpeta / "pagina.png").write_bytes(b"contenido")
    if testigo is not None:
        (carpeta / ".de_quien_es").write_text(testigo, encoding="utf-8")
    return carpeta


def test_no_borra_la_carpeta_de_un_proceso_vivo(raiz):
    """Otro proceso esta trabajando ahi ahora mismo: no se borra."""
    propiedad.registrar_proceso()
    otro = "testigo_de_otro_proceso"
    (raiz / ".procesos").mkdir(exist_ok=True)
    (raiz / ".procesos" / otro).write_text("pid=999", encoding="utf-8")

    viva = _carpeta(raiz, "trabajo_en_curso", otro)
    JobStore(workers=1).purge_previous_runs()

    assert viva.exists(), "se borro el trabajo de un proceso vivo"
    assert (viva / "pagina.png").exists()


def test_si_borra_los_restos_de_un_proceso_muerto(raiz):
    """Lo que la limpieza existe para hacer: no dejar PII en disco."""
    propiedad.registrar_proceso()
    muerto = "testigo_de_un_proceso_muerto"
    (raiz / ".procesos").mkdir(exist_ok=True)
    marca = raiz / ".procesos" / muerto
    marca.write_text("pid=999", encoding="utf-8")
    viejo = time.time() - propiedad._margen_frescura() - 60
    import os
    os.utime(marca, (viejo, viejo))

    resto = _carpeta(raiz, "resto_de_antes", muerto)
    JobStore(workers=1).purge_previous_runs()

    assert not resto.exists(), "un resto con datos personales se quedo en disco"


def test_borra_las_carpetas_sin_duenyo(raiz):
    """Sin marca = de una version anterior: no puede ser de nadie vivo."""
    propiedad.registrar_proceso()
    antigua = _carpeta(raiz, "de_una_version_anterior", None)
    JobStore(workers=1).purge_previous_runs()
    assert not antigua.exists()


def test_no_se_fia_del_pid(raiz, monkeypatch):
    """Un PID se reutiliza, y en el contenedor el del servicio suele ser 1.

    Fijar que la decision NO mira el PID: se cambia el que haya escrito en la
    marca por el de este mismo proceso —el caso de reutilizacion— y el
    resultado no puede cambiar, porque lo que decide es la frescura.
    """
    import os
    propiedad.registrar_proceso()
    muerto = "testigo_muerto"
    (raiz / ".procesos").mkdir(exist_ok=True)
    marca = raiz / ".procesos" / muerto
    marca.write_text(f"pid={os.getpid()}\n", encoding="utf-8")   # PID reutilizado
    viejo = time.time() - propiedad._margen_frescura() - 60
    os.utime(marca, (viejo, viejo))

    resto = _carpeta(raiz, "resto", muerto)
    JobStore(workers=1).purge_previous_runs()
    assert not resto.exists(), "dio por vivo un proceso muerto por coincidir el PID"


def test_en_el_contenedor_se_limpia_todo(raiz):
    """El directorio temporal vive en memoria y desaparece al reiniciar.

    Sin `.procesos`, no hay ninguna marca que respetar: todo lo que haya son
    restos y se borra, que es justo lo que debe pasar en un contenedor nuevo.
    """
    resto = _carpeta(raiz, "resto_con_pii", "testigo_de_un_contenedor_anterior")
    assert not (raiz / ".procesos").exists()
    JobStore(workers=1).purge_previous_runs()
    assert not resto.exists()


def test_el_propio_proceso_se_respeta_a_si_mismo(raiz):
    """Arrancar dos veces en el mismo proceso no puede borrarse su trabajo."""
    propiedad.registrar_proceso()
    mia = _carpeta(raiz, "mi_trabajo", propiedad.TESTIGO)
    JobStore(workers=1).purge_previous_runs()
    assert mia.exists()
