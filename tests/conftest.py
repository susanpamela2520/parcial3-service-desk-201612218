"""Configuración de pruebas.

Aislamiento: las pruebas SOLO usan DATABASE_URL_TEST (base catalogo_test). Se aborta si la URL no
apunta a una base cuyo nombre termine en "_test", para no destruir datos de evaluación.
"""
import os
import sys
from pathlib import Path

import pytest

URL_TEST = os.getenv("DATABASE_URL_TEST", "postgresql+psycopg://catalogo:catalogo@localhost:5432/catalogo_test")
if not URL_TEST.rsplit("/", 1)[-1].endswith("_test"):
    sys.exit(f"DATABASE_URL_TEST debe apuntar a una base *_test (recibido: {URL_TEST.rsplit('@', 1)[-1]})")
os.environ["DATABASE_URL"] = URL_TEST  # antes de importar app.*

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Area, Departamento, Empresa, Puesto, Seccion, Usuario  # noqa: E402
from app.security import hash_password  # noqa: E402
from scripts.migrate import migrar  # noqa: E402

PW_ADMIN = "Admin-Prueba-123"
PW_CONSULTA = "Consulta-Prueba-123"
TABLAS_DATOS = ["ticket_bitacora", "ticket_comentario", "ticket_adjunto", "ticket_pausa", "ticket_relacion", "ticket_ci",
                "notificacion", "ticket", "cola_miembro", "cola", "ci_servicio", "ci_relacion", "ci", "usuario_rol",
                "observacion_importacion", "importacion", "sesion", "servicio_n2", "servicio_n1", "mapeo_etiqueta",
                "usuario", "puesto", "seccion", "departamento", "area", "empresa"]


@pytest.fixture(scope="session", autouse=True)
def esquema():
    with engine.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    migrar()
    yield


@pytest.fixture(autouse=True)
def limpiar():
    with engine.begin() as c:
        c.execute(text("ALTER TABLE ticket_bitacora DISABLE TRIGGER tg_bitacora_inmutable"))
        c.execute(text(f"TRUNCATE {', '.join(TABLAS_DATOS)} RESTART IDENTITY CASCADE"))
        c.execute(text("ALTER TABLE ticket_bitacora ENABLE TRIGGER tg_bitacora_inmutable"))
        c.execute(text("DELETE FROM ci_clase WHERE codigo LIKE 'T-%'"))
    yield


@pytest.fixture
def db():
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture
def org(db):
    """Jerarquía mínima con dos secciones y usuarios de cada rol."""
    e = Empresa(codigo="EMP", nombre="Empresa Prueba"); db.add(e); db.flush()
    a = Area(empresa_id=e.id, codigo="TI", nombre="TI"); db.add(a); db.flush()
    d = Departamento(area_id=a.id, codigo="INF", nombre="Infra"); db.add(d); db.flush()
    s1 = Seccion(departamento_id=d.id, codigo="S1", nombre="Sección 1")
    s2 = Seccion(departamento_id=d.id, codigo="S2", nombre="Sección 2")
    db.add_all([s1, s2]); db.flush()
    p1 = Puesto(seccion_id=s1.id, codigo="P1", nombre="Puesto 1")
    p2 = Puesto(seccion_id=s2.id, codigo="P2", nombre="Puesto 2")
    db.add_all([p1, p2]); db.flush()
    admin = Usuario(nombre="Admin", usuario="admin", correo="admin@x.test", rol="administrador",
                    puesto_id=p1.id, password_hash=hash_password(PW_ADMIN))
    consulta = Usuario(nombre="Consulta", usuario="consulta", correo="consulta@x.test", rol="consulta",
                       puesto_id=p2.id, password_hash=hash_password(PW_CONSULTA))
    db.add_all([admin, consulta]); db.commit()
    return {"empresa": e, "area": a, "departamento": d, "s1": s1, "s2": s2, "p1": p1, "p2": p2,
            "admin": admin, "consulta": consulta}


@pytest.fixture
def cliente():
    with TestClient(app) as c:
        yield c


def login(c: TestClient, usuario: str, password: str):
    return c.post("/api/auth/login", json={"usuario": usuario, "password": password})


@pytest.fixture
def admin(org):
    with TestClient(app) as c:
        assert login(c, "admin", PW_ADMIN).status_code == 200
        yield c


@pytest.fixture
def consulta(org):
    with TestClient(app) as c:
        assert login(c, "consulta", PW_CONSULTA).status_code == 200
        yield c
