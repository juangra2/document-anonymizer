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

Y una deteccion que es una VARIANTE de un dato ya detectado exacto ("Luis
Molina Medina NIF /" junto a "Luis Molina Medina") no cuenta como falso
positivo: es el mismo dato con el limite mal.  Si es mas larga que el dato, la
barra se come lo de al lado: eso se cuenta como SOBRE-REDACCION y se publica al
lado del F1, porque es justo la queja del ayuntamiento.

Durante la transicion se guardan los dos numeros (`transicion_contabilidad.json`,
con `scripts/recontar_resultados.py`):

| | F1 por aparicion | F1 por valor | sobre-redaccion |
|---|---|---|---|
| **linea base** (antes del arreglo, reconstruida) | | | |
| digital | 0.9256 | 0.9230 | >= 69 |
| escaneado | 0.8535 | 0.8773 | — |
| hibrido | 0.7051 | 0.7158 | >= 1 |
| media | 0.8281 | 0.8387 | |
| **c1** (06-10-2026, exacta: zxing + CSV, sin el arreglo del bucle) | | | |
| digital | 0.9344 | 0.9344 | 390 (2891 caracteres) |
| escaneado * | 0.8598 | 0.8876 | 15 (78) |
| hibrido | 0.8698 | 0.9206 | 19 (184) |
| media | 0.8880 | 0.9142 | |

\* 2 escaneados agotaron el tiempo por pagina con la maquina cargada y salieron
enteros en negro.  Se repitieron con la maquina libre (ya con el arreglo del
bucle) y la fila lleva esa repeticion; con los dos en negro salia 0.9008 por
valor, inflado: una pagina tachada entera tapa, pero no por haber detectado.

Como leerlo: la mejora del pipeline es la diferencia entre filas de la misma
columna; la de la contabilidad, la diferencia entre columnas de la misma fila.
La linea base se reconstruye desde los FN/FP que guardo, sin la lista de
detecciones: pierde las variantes, asi que su sobre-redaccion es una cota
inferior.  c1 es la primera medida exacta.  Una version anterior de esta tabla
daba para c1 una precision por valor inflada: la reconstruccion ponia el texto
exacto de la etiqueta y borraba las variantes, que entonces contaban como
falsos positivos.
"Por aparicion" ya no es exactamente el F1 guardado (0.924 en digital) porque
incluye las exclusiones de defectos del corpus.
