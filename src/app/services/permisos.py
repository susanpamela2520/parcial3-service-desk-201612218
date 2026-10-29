"""Autorización del Service Desk (siempre en el servidor).

Reglas:
- Roles efectivos = usuario.rol ∪ usuario_rol.
- 'administrador' puede todo.
- 'consulta' lee todo y NUNCA modifica: pertenecer a una cola no le concede permisos de
  escritura (se filtran los permisos de escritura si el usuario no tiene un rol operativo).
- Permisos de cola (consultar, atender, transferir, aprobar, administrar) se obtienen de
  cola_miembro por usuario o por rol; se combinan con OR.
- 'aprobar' exige además el rol 'aprobador' (o administrador).
"""
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..models import Usuario
from .errores import NegocioError

ROLES_OPERATIVOS = {"administrador", "agente", "aprobador"}


def roles_de(db: Session, u: Usuario) -> set[str]:
    extra = {r for (r,) in db.execute(text("SELECT rol FROM usuario_rol WHERE usuario_id=:u"), {"u": u.id})}
    return {u.rol} | extra


@dataclass
class PermisosCola:
    consultar: bool = False
    atender: bool = False
    transferir: bool = False
    aprobar: bool = False
    administrar: bool = False


def permisos_cola(db: Session, u: Usuario, cola_id: int, roles: set[str] | None = None) -> PermisosCola:
    roles = roles if roles is not None else roles_de(db, u)
    if "administrador" in roles:
        return PermisosCola(True, True, True, True, True)
    fila = db.execute(text("""
        SELECT bool_or(m.puede_consultar), bool_or(m.puede_atender), bool_or(m.puede_transferir),
               bool_or(m.puede_aprobar), bool_or(m.puede_administrar)
        FROM cola_miembro m JOIN cola c ON c.id = m.cola_id
        WHERE m.cola_id = :c AND m.activo AND c.activo
          AND (m.usuario_id = :u OR m.rol = ANY(:roles))
    """), {"c": cola_id, "u": u.id, "roles": list(roles)}).one()
    p = PermisosCola(*[bool(x) for x in fila])
    if not (roles & ROLES_OPERATIVOS):  # consulta/solicitante: jamás escritura por membresía
        p.atender = p.transferir = p.aprobar = p.administrar = False
    if "aprobador" not in roles:
        p.aprobar = False
    if "consulta" in roles or p.consultar:
        p.consultar = True
    return p


def puede_ver_ticket(db: Session, u: Usuario, t: dict, roles: set[str] | None = None) -> bool:
    roles = roles if roles is not None else roles_de(db, u)
    if roles & {"administrador", "consulta"}:
        return True
    if u.id in (t["solicitante_id"], t["creador_id"], t.get("beneficiario_id")):
        return True
    return permisos_cola(db, u, t["cola_id"], roles).consultar


def es_agente_del_ticket(db: Session, u: Usuario, t: dict, roles: set[str] | None = None) -> bool:
    roles = roles if roles is not None else roles_de(db, u)
    return permisos_cola(db, u, t["cola_id"], roles).atender


def exigir_escritura(db: Session, u: Usuario) -> set[str]:
    roles = roles_de(db, u)
    if roles <= {"consulta"}:
        raise NegocioError("El rol de consulta no puede modificar información.", 403)
    return roles
