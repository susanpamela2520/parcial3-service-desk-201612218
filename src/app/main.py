"""Punto de entrada de la aplicación web."""
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .api import router
from .api_sd import router as router_sd
from .db import engine
from .services.errores import NegocioError

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Catálogo de Servicios de TI", version="3.0")
app.include_router(router)
app.include_router(router_sd)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.exception_handler(NegocioError)
def _negocio(_: Request, exc: NegocioError):
    return JSONResponse(status_code=exc.status, content={"detail": exc.mensaje, **exc.detalle})


@app.exception_handler(IntegrityError)
def _integridad(_: Request, exc: IntegrityError):
    # Última barrera: restricciones de la BD (únicos, FK, CHECK, trigger)
    msg = str(exc.orig).split("\n")[0]
    return JSONResponse(status_code=409, content={"detail": f"La base de datos rechazó la operación: {msg}"})


@app.get("/health")
def health():
    with engine.connect() as c:
        c.execute(text("SELECT 1"))
    return {"estado": "ok"}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
