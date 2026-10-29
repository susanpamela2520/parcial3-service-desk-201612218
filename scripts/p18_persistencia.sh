#!/bin/sh
# P18 (extremo a extremo): reiniciar contenedores SIN eliminar volúmenes no pierde catálogo, CMDB,
# tickets ni historial. Ejecutar desde el anfitrión con el sistema levantado:  sh scripts/p18_persistencia.sh
set -eu
CONTAR='from sqlalchemy import text
from app.db import engine
with engine.connect() as c:
    print(*c.execute(text("SELECT (SELECT count(*) FROM servicio_n2), (SELECT count(*) FROM ci), (SELECT count(*) FROM ticket), (SELECT count(*) FROM ticket_bitacora)")).one())'
antes=$(docker compose exec -T app python -c "$CONTAR")
echo "[P18] antes   (servicios_n2 ci tickets bitacora): $antes"
docker compose stop
docker compose start
for i in $(seq 1 30); do
  docker compose exec -T app python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/health')" 2>/dev/null && break
  sleep 2
done
despues=$(docker compose exec -T app python -c "$CONTAR")
echo "[P18] después (servicios_n2 ci tickets bitacora): $despues"
[ "$antes" = "$despues" ] && { echo "[P18] OK: sin pérdida de datos"; exit 0; }
echo "[P18] FALLO: los conteos cambiaron"; exit 1
