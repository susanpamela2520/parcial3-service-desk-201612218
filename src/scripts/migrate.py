"""Aplica en orden las migraciones SQL pendientes de /migrations.

Uso: python -m scripts.migrate
Registra cada archivo aplicado en schema_migrations; es seguro repetirlo.
Sale con código 0 si todo está al día, 1 si una migración falla.
"""
import sys
import time

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from app import config
from app.db import engine

MIGRATIONS_DIR = config.BASE_DIR / "migrations"


def esperar_bd(intentos: int = 30) -> None:
    for i in range(intentos):
        try:
            with engine.connect() as c:
                c.execute(text("SELECT 1"))
            return
        except OperationalError:
            print(f"[migrate] base de datos no disponible, reintento {i + 1}/{intentos}")
            time.sleep(2)
    raise SystemExit("[migrate] la base de datos no respondió")


def migrar() -> list[str]:
    aplicadas = []
    with engine.begin() as c:
        c.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " archivo VARCHAR(200) PRIMARY KEY, aplicada_en TIMESTAMPTZ NOT NULL DEFAULT now())"
        ))
        hechas = {r[0] for r in c.execute(text("SELECT archivo FROM schema_migrations"))}
    for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if f.name in hechas:
            continue
        with engine.begin() as c:
            # cursor del driver sin parámetros: el SQL puede contener '%' literales
            c.connection.dbapi_connection.cursor().execute(f.read_text(encoding="utf-8"))
            c.execute(text("INSERT INTO schema_migrations (archivo) VALUES (:a)"), {"a": f.name})
        aplicadas.append(f.name)
        print(f"[migrate] aplicada {f.name}")
    return aplicadas


def main() -> int:
    esperar_bd()
    try:
        aplicadas = migrar()
    except Exception as exc:  # noqa: BLE001 - se informa y se sale con error
        print(f"[migrate] ERROR: {exc}")
        return 1
    if not aplicadas:
        print("[migrate] esquema al día")
    return 0


if __name__ == "__main__":
    sys.exit(main())
