#!/bin/sh
# Harness de verificación (parcial 3): lo ejecutan el asistente de IA y el catedrático.
#   docker compose exec app sh scripts/check.sh
# Sale con 0 solo si TODOS los controles pasan. Las pruebas usan la base aislada catalogo_test;
# la base de evaluación solo recibe migraciones pendientes (no destructivo).
set -u
cd "$(dirname "$0")/.."
# Corrección del ciclo 01 (parcial 2): fuera del contenedor PYTHONPATH no existe
export PYTHONPATH="${PYTHONPATH:-src}"
fallos=0
paso() { printf '\n== %s ==\n' "$1"; }
ok()   { echo "[OK] $1"; }
mal()  { echo "[FALLO] $1"; fallos=$((fallos + 1)); }

paso "1/6 Archivos fuente intactos (SHA-256)"
(cd data && sha256sum -c CHECKSUMS.sha256) && ok "CatalogoServicios.xlsx y CMDB.xlsx sin cambios" || mal "un archivo fuente fue modificado"

paso "2/6 Calidad de código (ruff)"
ruff check src tests && ok "sin observaciones" || mal "ruff encontró problemas"

paso "3/6 Secretos fuera del repositorio"
if [ -f .env ]; then mal ".env presente en el contexto de la imagen/repositorio"; else ok ".env no incluido"; fi
if grep -rInE "(Admin|Consulta)-Demo-[0-9]{4}" src tests 2>/dev/null; then mal "contraseña de demostración escrita en el código"; else ok "sin contraseñas en el código"; fi

paso "4/6 Migraciones"
python -m scripts.migrate && ok "esquema al día" || mal "migraciones"

paso "5/6 Pruebas automatizadas P01-P17 + parcial 2 (base catalogo_test)"
python -m pytest -q -p no:warnings && ok "pruebas en verde" || mal "hay pruebas fallidas"

paso "6/6 Datos de evaluación (solo lectura)"
python - <<'PY' && ok "catálogo 12/46, >= 5,000 CI y >= 35 tickets" || mal "datos de evaluación incompletos (ejecute scripts/preparar.sh)"
from sqlalchemy import text
from app.db import engine
with engine.connect() as c:
    n1, n2 = c.execute(text("SELECT (SELECT count(*) FROM servicio_n1), (SELECT count(*) FROM servicio_n2 WHERE origen_hoja IS NOT NULL AND origen_hoja <> '' AND origen_hoja NOT LIKE 'Ticket%')")).one()
    ci = c.execute(text("SELECT count(DISTINCT codigo) FROM ci WHERE lote LIKE 'SEMILLA-%'")).scalar()
    tk = c.execute(text("SELECT count(*) FROM ticket WHERE lote='DEMO'")).scalar()
    print(f"N1={n1} N2(importados)={n2} CI(generados)={ci} tickets DEMO={tk}")
    raise SystemExit(0 if (n1, n2) == (12, 46) and ci >= 5000 and tk >= 35 else 1)
PY

echo
if [ "$fallos" -eq 0 ]; then echo "RESULTADO: TODO EN VERDE"; exit 0; fi
echo "RESULTADO: $fallos control(es) fallido(s)"; exit 1
