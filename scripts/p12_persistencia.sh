#!/bin/sh
# P12 (extremo a extremo): los datos sobreviven a un reinicio de contenedores SIN borrar volúmenes.
# Ejecutar desde el equipo anfitrión, con el sistema levantado:  sh scripts/p12_persistencia.sh
set -eu
marca="P12-$(date +%s)"
echo "[P12] creando registro de control $marca"
docker compose exec -T app python -m scripts.p12 crear "$marca"
echo "[P12] reiniciando contenedores (docker compose stop + start; el volumen se conserva)"
docker compose stop
docker compose start
for i in $(seq 1 30); do
  if docker compose exec -T app python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/health')" 2>/dev/null; then break; fi
  sleep 2
done
if docker compose exec -T app python -m scripts.p12 verificar "$marca"; then
  echo "[P12] OK: el registro persiste tras el reinicio"; exit 0
fi
echo "[P12] FALLO: el registro no persistió"; exit 1
