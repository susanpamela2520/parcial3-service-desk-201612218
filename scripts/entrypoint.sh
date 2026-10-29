#!/bin/sh
# Arranque del contenedor app: espera la BD, aplica migraciones y levanta el servidor.
set -e
python -m scripts.migrate
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
