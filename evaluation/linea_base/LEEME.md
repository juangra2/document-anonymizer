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
| digital | 0.9276 | 0.9262 | >= 69 |
| escaneado | 0.8495 | 0.8718 | — |
| hibrido | 0.7065 | 0.7179 | >= 1 |
| media | 0.8279 | 0.8386 | |
| **c1** (06-10-2026: zxing + CSV) | | | |
| digital | 0.9363 | 0.9376 | 390 (2891 caracteres) |
| escaneado * | 0.8559 | 0.8822 | 15 (78) |
| hibrido | 0.8690 | 0.9197 | 19 (184) |
| media | 0.8871 | 0.9132 | |
| **c2** (07-10-2026: todo lo de c1 y ademas bucle de MinerU, DNI con la letra leida como cifra, IBAN y correo por su forma, filtro institucional en los dos caminos, contacto en cabecera y pie, telefonos con puntos y prefijo, expediente/registro, direcciones en prosa y en varias lineas, Pg. y Partida) | | | |
| digital | 0.9352 | 0.9366 | 390 (2891) |
| escaneado | 0.9157 | 0.9486 | 15 (78) |
| hibrido | 0.8778 | 0.9267 | 19 (184) |
| media | 0.9096 | 0.9373 | |
| **c3** (14-10-2026: c2 y ademas barrido de todas las apariciones en el digital, texto de menos de 2 puntos fuera, cabecera y pie con mas tipos y con nombres, etiquetas de dato personal en el filtro institucional, pasaporte pegado, salto de linea a mitad de direccion) | | | |
| digital | 0.9470 | 0.9499 | 403 (2988) |
| escaneado | 0.9157 | 0.9486 | 15 (78) |
| hibrido | 0.8885 | 0.9330 | 20 (197) |
| media | 0.9171 | 0.9438 | |
| **c4** (19-10-2026: c3 y ademas imagenes pegadas leidas recortadas, formularios (registro, recibo, CSV, importe, lugar de nacimiento), celdas de MinerU, paginas giradas, dos columnas, firmas en fila, telefono partido) | | | |
| digital | 0.9471 | 0.9498 | 381 (2897) |
| escaneado | 0.9149 | 0.9474 | 15 (78) |
| hibrido | 0.9028 | 0.9345 | 20 (197) |
| media | 0.9216 | 0.9439 | |

Todas las filas recontadas el 07-10-2026 con el evaluador actual (incluidas las
exclusiones domicilio_institucional y lugar_infraccion_sin_etiquetar), asi que
son comparables entre si.

\* 2 escaneados de c1 agotaron el tiempo por pagina con la maquina cargada; la
fila lleva su repeticion con la maquina libre.

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
