# Service Desk de TI con CMDB — Parcial 3 (amplía el Parcial 2)

Software Avanzado, Facultad de Ingeniería, USAC. Aplicación web de Service Desk que amplía el sistema del catálogo de servicios del parcial práctico 2: tickets, colas, roles, siete procesos de gestión (lección 25) y una CMDB con 5,000 dispositivos sintéticos.

| | |
|---|---|
| **Integrante** | Susan — carné: `__________` |
| **Rama** | `main` |
| **Tecnologías** | Python 3.12, FastAPI, SQLAlchemy 2, PostgreSQL 16, HTML/JS, Docker Compose |

## Dónde está cada entregable
| Entregable | Archivo |
|---|---|
| Alcance, arquitectura, decisiones, limitaciones | [`docs/RESOLUCION.md`](docs/RESOLUCION.md) |
| Diagrama ER, diccionario, jerarquía CMDB, mapeo de fuentes | [`docs/MODELO.md`](docs/MODELO.md) |
| Tipos, colas, permisos, estados, transiciones, aprobaciones, escalamiento | [`docs/PROCESOS.md`](docs/PROCESOS.md) |
| Prompts, contexto, harness, loops y grafos (con commits) | [`docs/IA.md`](docs/IA.md) |
| Matriz requisito → implementación → prueba → evidencia y resultados | [`docs/PRUEBAS.md`](docs/PRUEBAS.md) |
| Contexto para asistentes de IA | [`AGENTS.md`](AGENTS.md), [`docs/contexto/`](docs/contexto/) |
| Evidencias (logs reales) | [`docs/evidencias/`](docs/evidencias/) (`parcial3/`, `loops/`) |
| Archivos fuente sin modificar | `data/CatalogoServicios.xlsx`, `data/CMDB.xlsx` (hash en `data/CHECKSUMS.sha256`), `docs/contexto/*.pdf` |
| Base del parcial 2 | [`docs/RESOLUCION-parcial2.md`](docs/RESOLUCION-parcial2.md) |

## 1. Requisitos
Docker Engine 24+ y Docker Compose v2.20+. Puerto 8000 libre (`APP_PORT`). No hace falta instalar Python, PostgreSQL ni claves de proveedores de IA.

## 2. Configuración
```bash
git clone <URL-del-repositorio> && cd <carpeta>
cp .env.example .env        # contraseñas SOLO de demostración; .env no se versiona
```

## 3. Levantar y preparar datos
```bash
docker compose up --build -d
docker compose ps                                   # db y app "healthy"
docker compose exec app sh scripts/preparar.sh     # idempotente: migra, importa catálogo, organización,
                                                    # CMDB.xlsx, 5,000 CI (semilla 2026) y datos del Service Desk
```
URL: **http://localhost:8000** · salud: `/health` · API interactiva: `/docs`.

`preparar.sh` ejecuta, en orden y dentro del contenedor: `scripts.migrate`, `scripts.import_excel` (12/46), `scripts.seed_demo` (organización y cuentas), `scripts.import_cmdb` (CI de CMDB.xlsx), `scripts.generar_cmdb` (5,000 CI) y `scripts.seed_service_desk` (usuarios por rol, 10 colas, 39 tickets en distintos estados con E0–E3 e I0–I6). Repetirlo no duplica nada.

## 4. Cuentas de evaluación (locales, de demostración)
| Usuario | Rol(es) | Contraseña (variable de `.env`) |
|---|---|---|
| `admin` | administrador | `DEMO_ADMIN_PASSWORD` |
| `consulta` | consulta (solo lectura) | `DEMO_CONSULTA_PASSWORD` |
| `solicitante` | solicitante | `DEMO_USUARIOS_PASSWORD` |
| `agente1`, `agente2` | agente | `DEMO_USUARIOS_PASSWORD` |
| `aprobador` | aprobador + agente | `DEMO_USUARIOS_PASSWORD` |

Vistas: **Portal** (solicitante: registrar, ver sus tickets, FAQ, notificaciones), **Consola** (agentes: filtros, ficha, toma, transiciones, transferir/escalar, comentarios, adjuntos, bitácora), **Colas** (configuración, miembros, tomar siguiente, niveles), **CMDB** (clasificación, búsqueda paginada de 5,000 CI, relaciones navegables, impacto), **Tablero** (indicadores), y las vistas del parcial 2.

## 5. Pruebas y verificación
```bash
docker compose exec app pytest -v                # P01–P17 + parcial 2, base aislada catalogo_test
docker compose exec app sh scripts/check.sh     # harness: fuentes intactas, lint, secretos, migraciones,
                                                 # pruebas y conteos 12/46/5000/35 -> exit 0/1
sh scripts/p18_persistencia.sh                  # P18 desde el anfitrión: reinicio sin perder datos
```
Generador manual: `docker compose exec app python -m scripts.generar_cmdb --semilla 2026 --cantidad 5000` (repetir no duplica). Borrado **explícito** de un lote: `… --semilla 2026 --borrar`.

> Si el volumen se creó antes de existir `catalogo_test`: `docker compose exec db psql -U catalogo -c "CREATE DATABASE catalogo_test;"`.

## 6. Operación
| Acción | Comando |
|---|---|
| Logs | `docker compose logs -f app` |
| Detener / iniciar (conserva datos) | `docker compose stop` / `docker compose start` |
| Apagar (conserva datos) | `docker compose down` |
| **Reinicio destructivo** (borra todo) | `docker compose down -v`, luego §3 |

## 7. Estructura
```text
AGENTS.md  compose.yaml  Dockerfile  .dockerignore  .env.example  requirements.txt
migrations/001–004            esquema (parcial 2 + Service Desk + CMDB)
src/app/                      api.py, api_sd.py, services/ (permisos, tickets, procesos, colas, cmdb, indicadores…), static/
src/scripts/                  migrate, import_excel, seed_demo, import_cmdb, generar_cmdb, seed_service_desk
scripts/                      preparar.sh, check.sh, p18_persistencia.sh, p12_persistencia.sh, entrypoint.sh
tests/                        test_service_desk.py (P01–P17) + pruebas del parcial 2
data/                         CatalogoServicios.xlsx, CMDB.xlsx, CHECKSUMS.sha256, referencia/
docs/                         RESOLUCION, MODELO, PROCESOS, IA, PRUEBAS, contexto/, evidencias/, prompts/
```

## 8. Acceso del catedrático
Colaborador `maldanap-usac` invitado (ver `docs/evidencias/invitacion-colaborador.png`).
