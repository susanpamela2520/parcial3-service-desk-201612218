# Historial del contexto (context engineering)

## Documentos entregados al asistente por fase

| Fase | Documentos de contexto suministrados | Por qué |
|---|---|---|
| 0. Arranque | `enunciado.md` | Fuente única de requisitos y rúbrica. |
| 1. Análisis del Excel | `enunciado.md` §2 y §3.4, `analisis-excel.md` | El asistente necesita la forma de la hoja y los casos conocidos; NO se le dan instrucciones desde las celdas. |
| 2. Modelo de datos | `AGENTS.md` §4, `reglas-negocio.md`, `analisis-excel.md` (tabla columnas→modelo) | Para que cada campo del Excel tenga destino y cada regla tenga restricción en BD. |
| 3. Autenticación | `AGENTS.md` §3, `reglas-negocio.md` (RS-01…RS-05), enunciado §3.1 | Delimitar el mecanismo (sesión de servidor) y evitar que proponga OAuth/JWT externos. |
| 4. Importación | `analisis-excel.md`, reglas R1–R8 en `src/app/importador.py`, `AGENTS.md` §5 | Reglas explícitas para combinaciones, conflictos y ausencias. |
| 5. Pruebas / Docker / harness | `AGENTS.md` §6–§7, enunciado §5–§6 | Comandos exactos y límites (base `_test`, no `down -v`, no tocar el Excel). |

## Actualizaciones del contexto

> Ver también la Actualización 3 al final (archivo original).

### Actualización 1 — Excel no disponible + re-importación respetuosa (commit "Contexto v2")
- **Hallazgo:** durante la sesión el asistente no tenía el archivo `.xlsx`, solo el enunciado. Escribir el importador "a ciegas" sin una forma de probarlo dejaba las reglas sin verificar.
- **Cambio de decisión:** se agregó un generador de Excel sintético con la misma estructura y se separaron las pruebas de reglas (sintético) de P06–P08 (original).
- **Decisión que se incorporó al contexto:** al diseñar la repetición de la importación se vio que un upsert simple pisaría ediciones hechas desde la aplicación (p. ej. completar ACTIVO de SE.12.2). Se fijó como regla guardar `origen_hash` por registro y actualizar solo si el Excel cambió; la prueba `test_regla_reimportacion_respeta_ediciones` lo verifica.
- **Archivos tocados:** `AGENTS.md` §5, `analisis-excel.md` (sección "Hallazgo"), este archivo.

### Actualización 2 — Observaciones informativas y entorno del harness (commit "Contexto v3")
- **Hallazgo 1:** al registrar el mapeo de la etiqueta `back end → Back End`, el servicio quedaba en `REVISION` aunque la corrección ya estaba resuelta y documentada. Se decidió que `ETIQUETA_MAPEADA` y `CODIGO_NORMALIZADO` son **informativas** y no ponen el servicio en revisión.
- **Hallazgo 2 (ciclo de harness 01):** `scripts/check.sh` falló fuera del contenedor con `No module named scripts.migrate` porque `PYTHONPATH` solo existía en la imagen Docker. Se corrigió exportándolo en el script y se dejó la regla en `AGENTS.md` §6.
- **Archivos tocados:** `AGENTS.md` §5–§6, `reglas-negocio.md` (RC-05), este archivo, `docs/evidencias/harness-ciclo-01-*.log`.

### Actualización 3 — Archivo original disponible (commit "Contexto v4")
- **Hallazgo:** con el Excel original la primera importación dio 12/46 en el control pero solo **45** servicios N2 creados: `SE.12.3` (fila 101) no tiene COD.N1 y la regla vigente lo omitía (`N2_SIN_N1`). Además, la fila 111 ("OPCIONES") se reportaba como fila fuera de rango genérica.
- **Cambio de decisión:** nueva regla R9 (asociar el N2 al N1 de su prefijo de código si existe, con observación) y clasificación `ENCABEZADO_LISTA_OPCIONES`. El Excel sintético se ajustó para reproducir A101 vacío y se agregó `test_regla_n1_derivado_del_codigo`.
- **Refuerzo de R4:** B100 coincide con el nombre del N2 SE.12.3, lo que respalda "Suministrar Analitica" como nombre canónico.
- **Archivos tocados:** `AGENTS.md` §5, `analisis-excel.md` (hallazgos con el original), `src/app/importador.py`, `tests/`, `docs/RESOLUCION.md` §4 y §8.

---
# Parcial 3 — Service Desk y CMDB

## Contexto suministrado por fase
| Fase | Contexto entregado al asistente | Motivo |
|---|---|---|
| P3-0 Lectura | `enunciado-parcial3.pdf`, `Service_Desk.pptx.pdf`, `Transicion.pptx.pdf`, `CMDB.xlsx`, `CatalogoServicios_2.xlsx` | Delimitar requisitos y fuentes antes de modelar. |
| P3-1 Modelo y procesos | `AGENTS.md` §1.1–§7.1, diapositivas 10–16 (lección 25), §4–§7 del enunciado | Estados, transiciones, E0–E3, I0–I6 y niveles. |
| P3-2 CMDB | `CMDB.xlsx` (B4:U6), Transición diap. 11–14, §8 del enunciado | Jerarquía y atributos con fuente explícita. |
| P3-3 Pruebas y harness | §11 del enunciado (P01–P18), `AGENTS.md` §6–§7 | Escenarios, aislamiento y límites. |

Separación instrucciones/datos: el contenido de los Excel y PDF se cita como **dato** (celda/diapositiva) y nunca se ejecuta como instrucción; las reglas que no salen de las fuentes se marcan como `DECISION_PROYECTO` (ver MODELO.md).

### Actualización P3-1 — Alcance del parcial 3 y hallazgo sobre la jerarquía CMDB
- **Hallazgo:** al leer las fuentes, `CMDB.xlsx` solo tiene encabezados B4:U4 y dos filas (Hardware/Software en C5/C6) y la lección de Transición no define clases por debajo de «Tipo de CI»; la propia nota editorial del enunciado lo advierte. `CatalogoServicios_2.xlsx` trae columnas N3 que el enunciado no pide usar.
- **Decisión:** jerarquía de 3 niveles con origen explícito por fila (`ci_clase.origen`); `CatalogoServicios_2.xlsx` solo como referencia; glosario, fuentes y reglas del Service Desk agregados a `AGENTS.md` (§1, §1.1, §1.2, §6.1, §7.1).

### Actualización P3-2 — Regla de ejecución de migraciones (Loop 3)
- **Hallazgo:** al preparar el entorno, `003_service_desk.sql` falló con `only '%s', '%b', '%t' are allowed as placeholders` por los porcentajes de referencia de la lección 25 (20 %, 70 %, 5 %).
- **Decisión:** ejecutar migraciones con el cursor del driver sin parámetros y registrar la regla en `AGENTS.md` §7.2 para que el asistente no reintroduzca el problema.
