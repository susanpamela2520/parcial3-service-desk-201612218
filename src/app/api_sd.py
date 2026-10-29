"""Rutas del Service Desk y la CMDB (parcial 3). Autorización siempre en el servidor."""
from fastapi import APIRouter, Body, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from .db import get_db
from .deps import usuario_actual
from .models import Usuario
from .services import cmdb, colas, indicadores
from .services import permisos as perm
from .services import tickets as tk
from .services.errores import NegocioError
from .services.procesos import FLUJOS, RESPUESTA_EVENTO

router = APIRouter(prefix="/api")
Lector = Depends(usuario_actual)


def _admin(db, u):
    if "administrador" not in perm.roles_de(db, u):
        raise NegocioError("Operación permitida solo para administradores.", 403)


# ------------------------------------------------------------------ referencia
@router.get("/procesos")
def procesos(_: Usuario = Lector, db: Session = Depends(get_db)):
    filas = [dict(r) for r in db.execute(text("SELECT * FROM proceso ORDER BY orden")).mappings()]
    for f in filas:
        fl = FLUJOS[f["codigo"]]
        f["estados"] = list(fl.estados)
        f["inicial"] = fl.inicial
        f["transiciones"] = [{"desde": t.desde, "hacia": t.hacia, "permiso": t.permiso, "requiere": list(t.requiere)}
                             for t in fl.transiciones]
    return {"procesos": filas, "eventos": RESPUESTA_EVENTO}


@router.get("/niveles-soporte")
def niveles(_: Usuario = Lector, db: Session = Depends(get_db)):
    return [dict(r) for r in db.execute(text("SELECT * FROM nivel_soporte ORDER BY nivel")).mappings()]


@router.put("/niveles-soporte/{nivel}")
def nivel_editar(nivel: int, datos: dict = Body(...), u: Usuario = Lector, db: Session = Depends(get_db)):
    _admin(db, u)
    db.execute(text("""INSERT INTO nivel_soporte (nivel, nombre, equivalencia, referencia_porcentaje, activo)
        VALUES (:n, :no, :eq, :rp, :ac) ON CONFLICT (nivel) DO UPDATE SET nombre=:no, equivalencia=:eq,
        referencia_porcentaje=:rp, activo=:ac"""), {"n": nivel, "no": datos.get("nombre"), "eq": datos.get("equivalencia", ""),
                                                   "rp": datos.get("referencia_porcentaje"), "ac": datos.get("activo", True)})
    db.commit()
    return {"ok": True}


@router.get("/mis-roles")
def mis_roles(u: Usuario = Lector, db: Session = Depends(get_db)):
    return {"roles": sorted(perm.roles_de(db, u))}


@router.get("/kb")
def kb(q: str | None = None, _: Usuario = Lector, db: Session = Depends(get_db)):
    return [dict(r) for r in db.execute(text("""SELECT id, titulo, contenido FROM kb_articulo WHERE activo
        AND (CAST(:q AS text) IS NULL OR titulo ILIKE :p OR contenido ILIKE :p) ORDER BY id"""),
                                        {"q": q, "p": f"%{q}%"}).mappings()]


@router.get("/notificaciones")
def notificaciones(u: Usuario = Lector, db: Session = Depends(get_db)):
    roles = perm.roles_de(db, u)
    todas = bool(roles & {"administrador", "consulta"})
    return [dict(r) for r in db.execute(text("""SELECT n.*, t.numero FROM notificacion n LEFT JOIN ticket t ON t.id=n.ticket_id
        WHERE :todas OR n.usuario_id=:u OR n.cola_id IN (SELECT cola_id FROM cola_miembro WHERE activo
          AND (usuario_id=:u OR rol = ANY(:roles))) ORDER BY n.id DESC LIMIT 100"""),
                                        {"todas": todas, "u": u.id, "roles": list(roles)}).mappings()]


# ------------------------------------------------------------------ colas
@router.get("/colas")
def colas_listar(_: Usuario = Lector, db: Session = Depends(get_db)):
    return colas.listar(db)


@router.post("/colas", status_code=201)
def colas_crear(datos: dict = Body(...), u: Usuario = Lector, db: Session = Depends(get_db)):
    return colas.guardar(db, u, datos)


@router.put("/colas/{cid}")
def colas_editar(cid: int, datos: dict = Body(...), u: Usuario = Lector, db: Session = Depends(get_db)):
    return colas.guardar(db, u, datos, cid)


@router.post("/colas/{cid}/desactivar")
def colas_desactivar(cid: int, cola_destino: int | None = None, u: Usuario = Lector, db: Session = Depends(get_db)):
    return colas.desactivar(db, u, cid, cola_destino)


@router.get("/colas/{cid}/miembros")
def colas_miembros(cid: int, _: Usuario = Lector, db: Session = Depends(get_db)):
    return colas.miembros(db, cid)


@router.post("/colas/{cid}/miembros", status_code=201)
def colas_miembro_agregar(cid: int, datos: dict = Body(...), u: Usuario = Lector, db: Session = Depends(get_db)):
    return colas.agregar_miembro(db, u, cid, datos)


@router.post("/colas/{cid}/miembros/{mid}/quitar")
def colas_miembro_quitar(cid: int, mid: int, u: Usuario = Lector, db: Session = Depends(get_db)):
    return colas.quitar_miembro(db, u, cid, mid)


@router.get("/colas/{cid}/siguientes")
def colas_siguientes(cid: int, u: Usuario = Lector, db: Session = Depends(get_db)):
    if not perm.permisos_cola(db, u, cid).consultar:
        raise NegocioError("No tiene acceso a esta cola.", 403)
    return tk.siguientes(db, cid)


@router.post("/colas/{cid}/tomar-siguiente")
def colas_tomar(cid: int, u: Usuario = Lector, db: Session = Depends(get_db)):
    return tk.tomar_siguiente(db, u, cid)


@router.get("/colas/{cid}/mis-permisos")
def colas_mis_permisos(cid: int, u: Usuario = Lector, db: Session = Depends(get_db)):
    return perm.permisos_cola(db, u, cid).__dict__


# ------------------------------------------------------------------ tickets
@router.get("/tickets")
def tickets_buscar(numero: str | None = None, servicio_n2_id: int | None = None, proceso: str | None = None,
                   cola_id: int | None = None, estado: str | None = None, prioridad: int | None = None,
                   responsable_id: int | None = None, desde: str | None = None, hasta: str | None = None,
                   mios: bool = False, sin_asignar: bool = False, abiertos: bool = False,
                   pagina: int = Query(1, ge=1), tamano: int = Query(20, ge=1, le=100),
                   u: Usuario = Lector, db: Session = Depends(get_db)):
    return tk.buscar(db, u, dict(numero=numero, servicio_n2_id=servicio_n2_id, proceso=proceso, cola_id=cola_id,
                                 estado=estado, prioridad=prioridad, responsable_id=responsable_id, desde=desde,
                                 hasta=hasta, mios=mios, sin_asignar=sin_asignar, abiertos=abiertos, pagina=pagina,
                                 tamano=tamano))


@router.post("/tickets", status_code=201)
def tickets_crear(datos: dict = Body(...), u: Usuario = Lector, db: Session = Depends(get_db)):
    return tk.crear(db, u, datos)


@router.get("/tickets/{tid}")
def tickets_detalle(tid: int, u: Usuario = Lector, db: Session = Depends(get_db)):
    return tk.detalle(db, u, tid)


@router.post("/tickets/{tid}/transicion")
def tickets_transicion(tid: int, datos: dict = Body(...), u: Usuario = Lector, db: Session = Depends(get_db)):
    tk.transicionar(db, u, tid, datos.get("hacia"), datos.get("datos"), datos.get("motivo"))
    return tk.detalle(db, u, tid)


@router.post("/tickets/{tid}/tomar")
def tickets_tomar(tid: int, u: Usuario = Lector, db: Session = Depends(get_db)):
    return tk.tomar(db, u, tid)


@router.post("/tickets/{tid}/asignar")
def tickets_asignar(tid: int, datos: dict = Body(...), u: Usuario = Lector, db: Session = Depends(get_db)):
    return tk.asignar(db, u, tid, datos.get("usuario_id"), datos.get("motivo"))


@router.post("/tickets/{tid}/transferir")
def tickets_transferir(tid: int, datos: dict = Body(...), u: Usuario = Lector, db: Session = Depends(get_db)):
    return tk.transferir(db, u, tid, datos.get("cola_id"), datos.get("motivo"))


@router.post("/tickets/{tid}/escalar")
def tickets_escalar(tid: int, datos: dict = Body(...), u: Usuario = Lector, db: Session = Depends(get_db)):
    return tk.transferir(db, u, tid, datos.get("cola_id"), datos.get("motivo"), escalar=True)


@router.post("/tickets/{tid}/reabrir")
def tickets_reabrir(tid: int, datos: dict = Body(...), u: Usuario = Lector, db: Session = Depends(get_db)):
    return tk.reabrir(db, u, tid, datos.get("motivo"))


@router.post("/tickets/{tid}/comentarios", status_code=201)
def tickets_comentar(tid: int, datos: dict = Body(...), u: Usuario = Lector, db: Session = Depends(get_db)):
    return tk.comentar(db, u, tid, datos.get("texto"), bool(datos.get("interno")))


@router.post("/tickets/{tid}/adjuntos", status_code=201)
async def tickets_adjuntar(tid: int, archivo: UploadFile = File(...), interno: bool = Form(False),
                           u: Usuario = Lector, db: Session = Depends(get_db)):
    contenido = await archivo.read(tk.MAX_ADJUNTO + 1)
    return tk.adjuntar(db, u, tid, archivo.filename, archivo.content_type, contenido, interno)


@router.get("/tickets/{tid}/adjuntos/{aid}")
def tickets_descargar(tid: int, aid: int, u: Usuario = Lector, db: Session = Depends(get_db)):
    a = tk.descargar(db, u, tid, aid)
    return Response(a["contenido"], media_type=a["tipo_mime"],
                    headers={"Content-Disposition": f'attachment; filename="{a["nombre"]}"',
                             "X-Content-Type-Options": "nosniff"})


@router.post("/tickets/{tid}/cis")
def tickets_cis(tid: int, datos: dict = Body(...), u: Usuario = Lector, db: Session = Depends(get_db)):
    return tk.asociar_cis(db, u, tid, datos.get("ci_ids") or [])


@router.post("/tickets/{tid}/relaciones", status_code=201)
def tickets_relacionar(tid: int, datos: dict = Body(...), u: Usuario = Lector, db: Session = Depends(get_db)):
    return tk.relacionar(db, u, tid, datos.get("destino_id"), datos.get("tipo"))


@router.post("/eventos", status_code=201)
def eventos(datos: dict = Body(...), u: Usuario = Lector, db: Session = Depends(get_db)):
    return tk.procesar_evento(db, u, datos)


@router.get("/indicadores")
def tablero(_: Usuario = Lector, db: Session = Depends(get_db)):
    return indicadores.calcular(db)


# ------------------------------------------------------------------ CMDB
@router.get("/cmdb/clases")
def cmdb_clases(_: Usuario = Lector, db: Session = Depends(get_db)):
    return cmdb.clases(db)


@router.post("/cmdb/clases", status_code=201)
def cmdb_clase_crear(datos: dict = Body(...), u: Usuario = Lector, db: Session = Depends(get_db)):
    return cmdb.guardar_clase(db, u, datos)


@router.put("/cmdb/clases/{cid}")
def cmdb_clase_editar(cid: int, datos: dict = Body(...), u: Usuario = Lector, db: Session = Depends(get_db)):
    return cmdb.guardar_clase(db, u, datos, cid)


@router.get("/cmdb/ubicaciones")
def cmdb_ubicaciones(_: Usuario = Lector, db: Session = Depends(get_db)):
    return [dict(r) for r in db.execute(text("SELECT * FROM ubicacion ORDER BY codigo")).mappings()]


@router.get("/cmdb/cis")
def cmdb_buscar(q: str | None = None, clase_id: int | None = None, estado: str | None = None,
                ubicacion_id: int | None = None, lote: str | None = None, servicio_n2_id: int | None = None,
                pagina: int = Query(1, ge=1), tamano: int = Query(25, ge=1, le=100),
                _: Usuario = Lector, db: Session = Depends(get_db)):
    return cmdb.buscar(db, dict(q=q, clase_id=clase_id, estado=estado, ubicacion_id=ubicacion_id, lote=lote,
                                servicio_n2_id=servicio_n2_id, pagina=pagina, tamano=tamano))


@router.get("/cmdb/cis/{cid}")
def cmdb_detalle(cid: int, _: Usuario = Lector, db: Session = Depends(get_db)):
    return cmdb.detalle(db, cid)


@router.get("/cmdb/cis/{cid}/impacto")
def cmdb_impacto(cid: int, _: Usuario = Lector, db: Session = Depends(get_db)):
    return cmdb.impacto(db, cid)


@router.post("/cmdb/relaciones", status_code=201)
def cmdb_relacionar(datos: dict = Body(...), u: Usuario = Lector, db: Session = Depends(get_db)):
    return cmdb.relacionar(db, u, datos.get("origen_id"), datos.get("destino_id"), datos.get("tipo"))


@router.get("/cmdb/resumen")
def cmdb_resumen(lote: str | None = None, _: Usuario = Lector, db: Session = Depends(get_db)):
    return cmdb.resumen(db, lote)
