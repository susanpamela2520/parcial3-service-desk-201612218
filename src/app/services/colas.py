"""Mantenimiento de colas y sus miembros. Solo administradores (o quien tenga 'administrar' en la cola)."""
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..models import Usuario
from . import permisos as perm
from .errores import NegocioError
from .tickets import registrar

ROLES = {"administrador", "consulta", "solicitante", "agente", "aprobador"}
CAMPOS = ("codigo", "nombre", "descripcion", "proceso_codigo", "nivel_soporte", "regla_orden",
          "objetivo_primera_atencion_min", "objetivo_resolucion_min")


def listar(db: Session) -> list[dict]:
    return [dict(r) for r in db.execute(text("""
        SELECT c.*, p.nombre AS proceso_nombre, n.nombre AS nivel_nombre,
          (SELECT count(*) FROM ticket t WHERE t.cola_id=c.id AND t.estado<>'CERRADO') AS abiertos
        FROM cola c JOIN proceso p ON p.codigo=c.proceso_codigo JOIN nivel_soporte n ON n.nivel=c.nivel_soporte
        ORDER BY p.orden, c.nivel_soporte, c.codigo""")).mappings()]


def miembros(db: Session, cola_id: int) -> list[dict]:
    return [dict(r) for r in db.execute(text("""SELECT m.*, u.usuario, u.nombre FROM cola_miembro m
        LEFT JOIN usuario u ON u.id=m.usuario_id WHERE m.cola_id=:c ORDER BY m.id"""), {"c": cola_id}).mappings()]


def _exigir_admin(db: Session, u: Usuario, cola_id: int | None = None):
    roles = perm.roles_de(db, u)
    if "administrador" in roles:
        return
    if cola_id and perm.permisos_cola(db, u, cola_id, roles).administrar:
        return
    raise NegocioError("Solo un administrador puede configurar colas.", 403)


def guardar(db: Session, u: Usuario, data: dict, cola_id: int | None = None) -> dict:
    _exigir_admin(db, u, cola_id)
    actual = {}
    if cola_id:
        actual = db.execute(text("SELECT * FROM cola WHERE id=:id"), {"id": cola_id}).mappings().first()
        if actual is None:
            raise NegocioError("La cola no existe.", 404)
        actual = dict(actual)
    v = {k: data.get(k, actual.get(k)) for k in CAMPOS}
    if not v["codigo"] or not v["nombre"]:
        raise NegocioError("Código y nombre son obligatorios.")
    if not db.execute(text("SELECT 1 FROM proceso WHERE codigo=:p"), {"p": v["proceso_codigo"]}).first():
        raise NegocioError("Cada cola debe configurar exactamente uno de los siete procesos.", 422)
    if not db.execute(text("SELECT 1 FROM nivel_soporte WHERE nivel=:n AND activo"), {"n": v["nivel_soporte"]}).first():
        raise NegocioError("Nivel de soporte inválido.", 422)
    v["regla_orden"] = v["regla_orden"] or "PRIORIDAD_ANTIGUEDAD"
    dup = db.execute(text("SELECT id FROM cola WHERE upper(codigo)=upper(:c) AND id<>:id"),
                     {"c": v["codigo"], "id": cola_id or 0}).first()
    if dup:
        raise NegocioError(f"Ya existe una cola con el código '{v['codigo']}'.", 409)
    if cola_id and v["proceso_codigo"] != actual["proceso_codigo"] and db.execute(
            text("SELECT 1 FROM ticket WHERE cola_id=:c AND estado<>'CERRADO'"), {"c": cola_id}).first():
        raise NegocioError("No se puede cambiar el proceso de una cola con tickets abiertos.", 409)
    if cola_id:
        db.execute(text(", ".join(f"{k}=:{k}" for k in CAMPOS).join(["UPDATE cola SET ", ", actualizado_en=now() WHERE id=:id"])),
                   {**v, "id": cola_id})
    else:
        cola_id = db.execute(text(f"INSERT INTO cola ({', '.join(CAMPOS)}) VALUES ({', '.join(':' + k for k in CAMPOS)}) "
                                  "RETURNING id"), v).scalar()
    db.commit()
    return dict(db.execute(text("SELECT * FROM cola WHERE id=:id"), {"id": cola_id}).mappings().first())


def desactivar(db: Session, u: Usuario, cola_id: int, cola_destino: int | None = None) -> dict:
    """No deja tickets huérfanos: con tickets abiertos exige una cola destino del mismo proceso."""
    _exigir_admin(db, u)
    c = db.execute(text("SELECT * FROM cola WHERE id=:id"), {"id": cola_id}).mappings().first()
    if c is None:
        raise NegocioError("La cola no existe.", 404)
    abiertos = [r[0] for r in db.execute(text("SELECT id FROM ticket WHERE cola_id=:c AND estado<>'CERRADO'"), {"c": cola_id})]
    if abiertos:
        if not cola_destino:
            raise NegocioError(f"La cola tiene {len(abiertos)} ticket(s) abierto(s). Indique cola_destino para moverlos.", 409,
                               {"tickets_abiertos": len(abiertos)})
        d = db.execute(text("SELECT * FROM cola WHERE id=:id AND activo"), {"id": cola_destino}).mappings().first()
        if d is None or d["proceso_codigo"] != c["proceso_codigo"] or d["id"] == c["id"]:
            raise NegocioError("La cola destino debe estar activa, ser distinta y del mismo proceso.", 422)
        for tid in abiertos:
            db.execute(text("UPDATE ticket SET cola_id=:d, nivel_soporte=:n, responsable_id=NULL, version=version+1, "
                            "actualizado_en=now() WHERE id=:t"), {"d": d["id"], "n": d["nivel_soporte"], "t": tid})
            registrar(db, tid, u.id, "TRANSFERENCIA", {"cola_id": c["id"]}, {"cola_id": d["id"]},
                      f"Desactivación de la cola {c['codigo']}")
    db.execute(text("UPDATE cola SET activo=FALSE, actualizado_en=now() WHERE id=:id"), {"id": cola_id})
    db.commit()
    return {"desactivada": c["codigo"], "tickets_movidos": len(abiertos)}


def agregar_miembro(db: Session, u: Usuario, cola_id: int, data: dict) -> dict:
    _exigir_admin(db, u, cola_id)
    uid, rol = data.get("usuario_id"), data.get("rol")
    if bool(uid) == bool(rol):
        raise NegocioError("Indique un usuario o un rol (exactamente uno).")
    if rol and rol not in ROLES:
        raise NegocioError(f"Rol inválido. Use {sorted(ROLES)}.")
    if uid and not db.execute(text("SELECT 1 FROM usuario WHERE id=:u AND activo"), {"u": uid}).first():
        raise NegocioError("El usuario no existe o está inactivo.", 404)
    flags = {k: bool(data.get(k, k == "puede_consultar")) for k in
             ("puede_consultar", "puede_atender", "puede_transferir", "puede_aprobar", "puede_administrar")}
    mid = db.execute(text("""INSERT INTO cola_miembro (cola_id, usuario_id, rol, puede_consultar, puede_atender,
        puede_transferir, puede_aprobar, puede_administrar) VALUES (:c, :u, :r, :puede_consultar, :puede_atender,
        :puede_transferir, :puede_aprobar, :puede_administrar)
        ON CONFLICT DO NOTHING RETURNING id"""), {"c": cola_id, "u": uid, "r": rol, **flags}).scalar()
    if mid is None:
        raise NegocioError("Ese usuario o rol ya es miembro de la cola.", 409)
    db.commit()
    return {"id": mid}


def quitar_miembro(db: Session, u: Usuario, cola_id: int, miembro_id: int) -> dict:
    _exigir_admin(db, u, cola_id)
    db.execute(text("UPDATE cola_miembro SET activo=FALSE WHERE id=:m AND cola_id=:c"), {"m": miembro_id, "c": cola_id})
    db.commit()
    return {"ok": True}


def liberar_tickets_de_usuario(db: Session, actor_id: int, usuario_id: int):
    """Al desactivar un usuario, sus tickets abiertos vuelven a la cola (sin responsable)."""
    for (tid,) in db.execute(text("SELECT id FROM ticket WHERE responsable_id=:u AND estado<>'CERRADO'"), {"u": usuario_id}).all():
        db.execute(text("UPDATE ticket SET responsable_id=NULL, version=version+1, actualizado_en=now() WHERE id=:t"),
                   {"t": tid})
        registrar(db, tid, actor_id, "LIBERACION", {"responsable_id": usuario_id}, {"responsable_id": None},
                  "Usuario responsable desactivado")
