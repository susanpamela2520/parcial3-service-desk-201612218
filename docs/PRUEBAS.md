# Pruebas: matriz y resultados reales

**Cómo se ejecutan**
```bash
docker compose exec app pytest -v                 # P01–P17 + pruebas del parcial 2 (base aislada catalogo_test)
docker compose exec app sh scripts/check.sh      # harness completo (código de salida 0/1)
sh scripts/p18_persistencia.sh                   # P18, desde el anfitrión (reinicia contenedores)
```
Aislamiento: las pruebas crean su propio esquema en `catalogo_test` y lo limpian por prueba (`tests/conftest.py`); nunca tocan la base `catalogo` de evaluación. Tipos: integración API + PostgreSQL (`TestClient`) para P01–P17; unitarias/integración del importador (parcial 2, `test_regla_*`); extremo a extremo para P18 (contenedores reales).

## 1. Matriz requisito → implementación → prueba → evidencia
| ID | Requisito (enunciado §11) | Implementación | Prueba | Evidencia |
|---|---|---|---|---|
| P01 | Autenticación, cierre de sesión, usuario inactivo, rechazo sin permiso | `security.py`, `deps.py`, `permisos.exigir_escritura`, `colas._exigir_admin` | `test_p01_autenticacion_roles_y_rechazos` (+ `test_auth.py` del parcial 2) | `parcial3/pytest-v.log` |
| P02 | Importación repetible 12/46 con incidencias | `importador.py` (R1–R9) | `test_p02_importacion_repetible_controles`, `test_importacion.py` | `importacion-original-sesion-ia.log` |
| P03 | ≥ 5,000 dispositivos únicos; repetir sin duplicar | `scripts/generar_cmdb.py` | `test_p03_generador_5000_unicos_y_repetible` | `parcial3/preparar.log` |
| P04 | Jerarquía = Excel + lección; padres inexistentes y ciclos rechazados | `ci_clase` + `origen`, `cmdb.guardar_clase`, trigger | `test_p04_jerarquia_fuentes_padres_y_ciclos` | `MODELO.md` §3 |
| P05 | Tickets con referencias válidas / inválidas | `tickets.crear` | `test_p05_creacion_con_referencias_validas_e_invalidas` | pytest |
| P06 | Cola asignada a roles y usuarios; acceso permitido/denegado | `cola_miembro`, `permisos_cola` | `test_p06_cola_por_rol_y_usuario_permisos` | pytest; Loop 2 |
| P07 | Orden reproducible; dos agentes, un ganador | `tickets.siguientes/tomar/tomar_siguiente` | `test_p07_ordenamiento_y_toma_concurrente` (2 hilos con barrera) | pytest |
| P08 | Transferencia con historial y validación de proceso | `tickets.transferir` | `test_p08_transferencia_con_historial_y_validacion` | pytest |
| P09 | Ruta completa de los 7 procesos; transiciones inválidas | `procesos.FLUJOS`, `tickets.transicionar` | `test_p09_…[7 procesos]` | Loop 1 |
| P10 | Cambio sin aprobación y acceso autoaprobado rechazados | `transicionar` + `CHECK` | `test_p10_cambio_sin_aprobacion_y_autoaprobacion` | pytest |
| P11 | E0–E3 con su respuesta; E2/E3 → incidente sin duplicar | `procesar_evento`, `_efecto_evento`, `huella_evento` única | `test_p11_eventos_e0_e3_y_sin_duplicar` | pytest |
| P12 | I0–I6 y reglas complementarias | `categoria_por_duracion`, `PRIORIDAD_INCIDENTE`, `REDUNDANCIA_INCIDENTE` | `test_p12_incidentes_i0_i6_reglas_complementarias` | `PROCESOS.md` §7 |
| P13 | Incidente → problema → cambio, trazable | `ticket_relacion` + reglas por proceso | `test_p13_incidente_problema_cambio_trazable` | pytest |
| P14 | Recorrido CI–servicio; ciclos según tipo | `cmdb.relacionar`, `cmdb.impacto` | `test_p14_recorrido_ci_servicio_y_ciclos` | `IA.md` §5 |
| P15 | Cierre sin resolución, reapertura y bitácora | flujos, `tickets.reabrir`, trigger inmutable | `test_p15_cierre_sin_resolucion_reapertura_bitacora` | pytest |
| P16 | Filtros y paginación con 5,000; tiempos observados | `cmdb.buscar` (`tiempo_ms`) | `test_p16_filtros_y_paginacion_5000` | tiempos en `parcial3/pytest-v.log` |
| P17 | Indicadores con datos controlados, pausas y abiertos | `indicadores.calcular` | `test_p17_indicadores_con_datos_controlados` | `PROCESOS.md` §8 |
| P18 | Reinicio sin pérdida de catálogo, CMDB, tickets e historial | volumen `pgdata` | `scripts/p18_persistencia.sh` | `evidencias/resultados-evaluacion-p3.md` |
| — | Adjuntos (tipo, tamaño, autorización) y notas internas | `tickets.adjuntar/descargar/comentar` | `test_adjuntos_comentarios_internos_y_autorizacion` | pytest |
| — | Sin tickets huérfanos al desactivar usuario o cola | `colas.desactivar`, `liberar_tickets_de_usuario` | `test_sin_huerfanos_al_desactivar_usuario_y_cola` | pytest |

## 2. Resultados obtenidos
Entorno de la sesión de IA: Linux, Python 3.13, PostgreSQL 16 local (sin Docker: el registro de imágenes no era accesible desde la sesión). Fecha: 2026-10-29.

| ID | Comando | Entrada | Esperado | Obtenido | Commit |
|---|---|---|---|---|---|
| P01 | `pytest -k p01` | usuarios por rol; logout; desactivar ag1 | 401/403 donde corresponde | PASSED | `6c07682` |
| P02 | `pytest -k p02` | `data/CatalogoServicios.xlsx` ×2 | 12/46; 2.ª vez 0 creados | PASSED | `6c07682` |
| P03 | `pytest -k p03` | semilla 77, 5,000 ×2 | 5,000 únicos; 2.ª vez 0 | PASSED | `6c07682` |
| P04 | `pytest -k p04` | padre 99999; ciclo A↔B | 404; 422; trigger | PASSED | `6c07682` |
| P05 | `pytest -k p05` | servicio/proceso/CI/cola inválidos | 404/422 | PASSED | `6c07682` |
| P06 | `pytest -k p06` | consulta miembro con «atender» | lectura 200, toma 403 | PASSED | `6c07682` |
| P07 | `pytest -k p07` | 3 tickets; 2 hilos sobre el mismo | orden alta1, alta2, baja; [200, 409] | PASSED | `6c07682` |
| P08 | `pytest -k p08` | sin motivo; otro proceso; válido; escalar | 422; 422; 200 + bitácora; nivel 2 | PASSED | `6c07682` |
| P09 | `pytest -k p09` | 7 rutas + 7 transiciones inválidas | CERRADO con bitácora; 409 | 7 PASSED (tras Loop 1) | `6c07682` |
| P10 | `pytest -k p10` | ejecutar sin aprobar; autoaprobar | 409; 403; CHECK en BD | PASSED | `6c07682` |
| P11 | `pytest -k p11` | huellas H-E0…H-E3; repetir H-E2 | respuestas por categoría; sin duplicar | PASSED | `6c07682` |
| P12 | `pytest -k p12` | 7 categorías; I1 con 600 min | prioridad/redundancia; 422 «sugerida I3» | PASSED | `6c07682` |
| P13 | `pytest -k p13` | relación invertida; válidas | 422; relaciones recuperables | PASSED | `6c07682` |
| P14 | `pytest -k p14` | APP1→BD1→SRV1; ciclo DEPENDE_DE; ciclo CONECTADO_A | 422 / 201; servicios SE.01.02 | PASSED | `6c07682` |
| P15 | `pytest -k p15` | cerrar en atención; reabrir sin motivo / otro usuario | 409; 422; 403; REAPERTURA con actor | PASSED | `6c07682` |
| P16 | `pytest -k p16 -s` | 5,000 CI; página 3 de 50; clase HW; texto | totales coherentes | PASSED. Tiempos observados (servidor, BD local): 13.63 ms página, 13.68 ms clase, 13.42 ms búsqueda | `6c07682` |
| P17 | `pytest -k p17` | 3 tickets con tiempos fijos y 60 min de pausa | 1.ª atención 32.5; resolución 420; 50 %/50 % | PASSED | `6c07682` |
| P18 | `sh scripts/p18_persistencia.sh` | sistema en Docker | conteos iguales antes/después | **Ejecutar en Docker** y registrar en `evidencias/resultados-evaluacion-p3.md` | — |
| Todas | `sh scripts/check.sh` | — | exit 0 | `RESULTADO: TODO EN VERDE`, 58 pruebas, exit 0 (`parcial3/check.log`) | `d44779e`+ |

Los tiempos de P16 son mediciones reales del entorno indicado; en Docker variarán. No se publican cifras no medidas.
