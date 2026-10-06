# Linea base del 28-09-2026, ANTES del arreglo unificado

| corpus | documentos | F1 | precision | recall |
|---|---|---|---|---|
| digital | 400 | 0.9240 | 0.9196 | 0.9286 |
| escaneado | 32 | 0.7489 | 0.7871 | 0.7143 |
| hibrido | 26 | 0.7425 | 0.9544 | 0.6076 |
| **media** | | **0.8051** | | |

Camino: digital 400/400 correcto, escaneado 32/32 correcto, hibrido **26/26
desviados al camino digital** — el defecto que el arreglo tiene que eliminar.

## Como comparar despues del arreglo

Contra **estos mismos documentos**, no contra el corpus completo. `subconjunto.json`
lleva el sha256 de cada PDF: si un fichero cambia, la comparacion deja de ser valida.

    python -m evaluation.run --input-pdfs ../corpus-generator/output/scanned_mitad_pdfs/ ...
    python -m evaluation.run --input-pdfs ../corpus-generator/output/hybrid_mitad_pdfs/ ...
    python -m evaluation.agregado

La otra mitad de cada corpus queda sin evaluar a proposito: los 17-18 puntos de
diferencia ya deciden, y habria que repetirla despues del arreglo igualmente.

## Lo que estas cifras NO miden

El evaluador compara listas de entidades; no abre el PDF de salida. Una barra
negra pintada al lado del dato cuenta como acierto. El desplazamiento de barras
del camino hibrido esta documentado a mano (samples/mixtos), no en estas cifras.

## Contabilidad nueva (06-10-2026): por valor y con paginas en negro

El evaluador cuenta ahora cada valor UNA vez por documento ("¿se detecto este
valor?"), no aparicion por aparicion; si cada aparicion queda tapada lo mide la
lectura de barras.  Y un valor que solo aparece en paginas que salen enteras en
negro (>= 90 % de pixeles oscuros en la salida) cuenta como detectado.  Ademas
quedan fuera los defectos conocidos del corpus —el telefono "(618) 741-017" y
los 10 QR fantasma del hibrido—, listados con lo que hay que corregir en el
generador y la exclusion que hay que quitar al regenerar en UN solo sitio:
`evaluation/defectos_corpus.py`.  Desde este cambio el evaluador guarda en cada
resultado la lista de detecciones y las paginas en negro, asi que las proximas
medidas se recuentan exactas, no reconstruidas.

Durante la transicion se guardan los dos numeros (`transicion_contabilidad.json`,
reconstruido con `scripts/recontar_resultados.py`):

| | F1 por aparicion | F1 por valor |
|---|---|---|
| **linea base** (antes del arreglo) | | |
| digital | 0.9256 | 0.9135 |
| escaneado | 0.8535 | 0.8773 |
| hibrido | 0.7051 | 0.7131 |
| media | 0.8281 | 0.8346 |
| **actual** (complemento B + red) | | |
| digital | 0.9256 | 0.9135 |
| escaneado | 0.8540 | 0.8771 |
| hibrido | 0.8642 | 0.9105 |
| media | 0.8813 | 0.9004 |

Como leerlo: la mejora del pipeline es la diferencia entre filas de la misma
columna; la de la contabilidad, la diferencia entre columnas de la misma fila.
El digital BAJA por valor: los aciertos repetidos (un nombre detectado diez
veces) inflaban la precision, y los falsos positivos casi nunca se repiten.
"Por aparicion" ya no es exactamente el F1 guardado (0.924 en digital) porque
incluye la exclusion del telefono del generador.
