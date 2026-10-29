#!/bin/sh
# Preparación completa del entorno de evaluación (idempotente; no borra nada):
#   docker compose exec app sh scripts/preparar.sh
# Orden: migraciones -> catálogo (Excel) -> organización/cuentas -> CMDB.xlsx -> 5,000 CI -> Service Desk.
set -e
cd "$(dirname "$0")/.."
export PYTHONPATH="${PYTHONPATH:-src}"
python -m scripts.migrate
python -m scripts.import_excel || [ $? -eq 2 ]
python -m scripts.seed_demo
python -m scripts.import_cmdb
python -m scripts.generar_cmdb --semilla "${CMDB_SEMILLA:-2026}" --cantidad "${CMDB_CANTIDAD:-5000}"
python -m scripts.seed_service_desk
echo "[preparar] listo"
