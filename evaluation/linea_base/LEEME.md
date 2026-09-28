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
