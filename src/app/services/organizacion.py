"""Reglas de la jerarquía Empresa → Área → Departamento → Sección → Puesto → Usuario.

Política de desactivación (baja lógica, nunca DELETE):
- Si el registro tiene hijos activos, se rechaza con 409 y se listan los hijos.
- Con cascada=True se desactivan todos los descendientes activos y se devuelve la lista.
- Desactivar usuarios revoca sus sesiones.
- Reactivar exige que el padre esté activo.
- No se crean ni mueven registros bajo un padre inactivo o inexistente.
"""
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..models import Area, Departamento, Empresa, Puesto, Seccion, ServicioN2, Usuario
from ..security import hash_password, revocar_sesiones_usuario
from .errores import NegocioError


@dataclass(frozen=True)
class Spec:
    modelo: type
    etiqueta: str
    campo_padre: str | None = None
    padre: type | None = None
    etiqueta_padre: str | None = None


ENTIDADES: dict[str, Spec] = {
    "empresas": Spec(Empresa, "empresa"),
    "areas": Spec(Area, "área", "empresa_id", Empresa, "empresa"),
    "departamentos": Spec(Departamento, "departamento", "area_id", Area, "área"),
    "secciones": Spec(Seccion, "sección", "departamento_id", Departamento, "departamento"),
    "puestos": Spec(Puesto, "puesto", "seccion_id", Seccion, "sección"),
}

# Hijos directos de cada modelo: (modelo hijo, campo FK, etiqueta)
HIJOS = {
    Empresa: (Area, "empresa_id", "áreas"),
    Area: (Departamento, "area_id", "departamentos"),
    Departamento: (Seccion, "departamento_id", "secciones"),
    Seccion: (Puesto, "seccion_id", "puestos"),
    Puesto: (Usuario, "puesto_id", "usuarios"),
}


def spec_de(entidad: str) -> Spec:
    if entidad not in ENTIDADES:
        raise NegocioError(f"Entidad desconocida: {entidad}", 404)
    return ENTIDADES[entidad]


def _texto(data: dict, campo: str, requerido: bool = True, maximo: int = 200) -> str | None:
    v = data.get(campo)
    v = v.strip() if isinstance(v, str) else v
    if not v:
        if requerido:
            raise NegocioError(f"El campo '{campo}' es obligatorio.")
        return None
    if not isinstance(v, str) or len(v) > maximo:
        raise NegocioError(f"El campo '{campo}' debe ser texto de hasta {maximo} caracteres.")
    return v


def _padre_activo(db: Session, spec: Spec, padre_id) -> None:
    if padre_id in (None, ""):
        raise NegocioError(f"Debe indicar la {spec.etiqueta_padre} a la que pertenece.")
    try:
        padre_id = int(padre_id)
    except (TypeError, ValueError):
        raise NegocioError(f"Identificador de {spec.etiqueta_padre} inválido.")
    padre = db.get(spec.padre, padre_id)
    if padre is None:
        raise NegocioError(f"La {spec.etiqueta_padre} indicada (id {padre_id}) no existe.", 404)
    if not padre.activo:
        raise NegocioError(f"No se puede asociar a la {spec.etiqueta_padre} '{padre.codigo}' porque está inactiva.", 409)


def _codigo_unico(db: Session, spec: Spec, codigo: str, padre_id, excluir_id: int | None = None) -> None:
    q = select(spec.modelo.id).where(func.upper(spec.modelo.codigo) == codigo.upper())
    if spec.campo_padre:
        q = q.where(getattr(spec.modelo, spec.campo_padre) == int(padre_id))
    if excluir_id:
        q = q.where(spec.modelo.id != excluir_id)
    if db.scalar(q) is not None:
        ambito = f" dentro de la {spec.etiqueta_padre} seleccionada" if spec.campo_padre else ""
        raise NegocioError(f"Ya existe un(a) {spec.etiqueta} con el código '{codigo}'{ambito}.", 409)


def a_dict(obj) -> dict:
    d = {c.key: getattr(obj, c.key) for c in obj.__table__.columns}
    d.pop("password_hash", None)  # nunca se expone el hash
    return d


def listar(db: Session, entidad: str, padre_id: int | None = None, activo: bool | None = None) -> list[dict]:
    spec = spec_de(entidad)
    q = select(spec.modelo).order_by(spec.modelo.codigo)
    if padre_id is not None and spec.campo_padre:
        q = q.where(getattr(spec.modelo, spec.campo_padre) == padre_id)
    if activo is not None:
        q = q.where(spec.modelo.activo == activo)
    return [a_dict(o) for o in db.scalars(q)]


def obtener(db: Session, entidad: str, id_: int):
    spec = spec_de(entidad)
    obj = db.get(spec.modelo, id_)
    if obj is None:
        raise NegocioError(f"No existe {spec.etiqueta} con id {id_}.", 404)
    return obj


def crear(db: Session, entidad: str, data: dict) -> dict:
    spec = spec_de(entidad)
    codigo = _texto(data, "codigo", maximo=30)
    nombre = _texto(data, "nombre")
    campos = {"codigo": codigo, "nombre": nombre}
    padre_id = None
    if spec.campo_padre:
        padre_id = data.get(spec.campo_padre)
        _padre_activo(db, spec, padre_id)
        campos[spec.campo_padre] = int(padre_id)
    _codigo_unico(db, spec, codigo, padre_id)
    obj = spec.modelo(**campos, activo=True)
    db.add(obj)
    db.commit()
    return a_dict(obj)


def actualizar(db: Session, entidad: str, id_: int, data: dict) -> dict:
    spec = spec_de(entidad)
    obj = obtener(db, entidad, id_)
    codigo = _texto(data, "codigo", requerido=False, maximo=30) or obj.codigo
    nombre = _texto(data, "nombre", requerido=False) or obj.nombre
    padre_id = getattr(obj, spec.campo_padre) if spec.campo_padre else None
    if spec.campo_padre and data.get(spec.campo_padre) not in (None, "") and int(data[spec.campo_padre]) != padre_id:
        _padre_activo(db, spec, data[spec.campo_padre])
        padre_id = int(data[spec.campo_padre])
        setattr(obj, spec.campo_padre, padre_id)
    _codigo_unico(db, spec, codigo, padre_id, excluir_id=obj.id)
    obj.codigo, obj.nombre = codigo, nombre
    db.commit()
    return a_dict(obj)


def _descendientes_activos(db: Session, obj) -> list:
    """Devuelve todos los descendientes activos (en profundidad)."""
    if type(obj) not in HIJOS:
        return []
    modelo_hijo, fk, _ = HIJOS[type(obj)]
    resultado = []
    for h in db.scalars(select(modelo_hijo).where(getattr(modelo_hijo, fk) == obj.id, modelo_hijo.activo.is_(True))):
        resultado.append(h)
        resultado.extend(_descendientes_activos(db, h))
    return resultado


def _describir(o) -> dict:
    return {"tipo": o.__tablename__, "id": o.id, "codigo": getattr(o, "codigo", None) or getattr(o, "usuario", None)}


def desactivar_registro(db: Session, obj, cascada: bool = False, usuario_actual_id: int | None = None) -> dict:
    if isinstance(obj, Usuario) and obj.id == usuario_actual_id:
        raise NegocioError("No puede desactivar su propio usuario.", 409)
    descendientes = _descendientes_activos(db, obj)
    if descendientes and not cascada:
        raise NegocioError(
            f"No se puede desactivar: tiene {len(descendientes)} registro(s) dependiente(s) activo(s). "
            "Desactívelos primero o confirme la desactivación en cascada.",
            409, {"dependientes": [_describir(d) for d in descendientes]},
        )
    afectados = [obj] + descendientes
    for o in afectados:
        o.activo = False
        if isinstance(o, Usuario):
            revocar_sesiones_usuario(db, o.id)
            from .colas import liberar_tickets_de_usuario  # parcial 3: sin tickets huérfanos
            liberar_tickets_de_usuario(db, usuario_actual_id or o.id, o.id)
    # Las secciones desactivadas conservan sus asignaciones históricas; se informan.
    secciones = [o.id for o in afectados if isinstance(o, Seccion)]
    servicios = []
    if secciones:
        servicios = list(db.scalars(select(ServicioN2.codigo).where(ServicioN2.seccion_id.in_(secciones))))
    db.commit()
    return {
        "desactivados": [_describir(o) for o in afectados],
        "servicios_con_seccion_inactiva": servicios,
    }


def activar_registro(db: Session, obj, spec_padre: tuple | None) -> dict:
    if spec_padre:
        modelo_padre, fk, etiqueta = spec_padre
        padre = db.get(modelo_padre, getattr(obj, fk))
        if padre is None or not padre.activo:
            raise NegocioError(f"No se puede reactivar porque su {etiqueta} está inactiva.", 409)
    obj.activo = True
    db.commit()
    return a_dict(obj)


def padre_de(obj) -> tuple | None:
    for padre, (hijo, fk, _) in HIJOS.items():
        if isinstance(obj, hijo):
            return padre, fk, padre.__tablename__
    return None


# ---------------------------------------------------------------- usuarios

ROLES = ("administrador", "consulta", "solicitante", "agente", "aprobador")


def ruta_organizacional(db: Session, puesto_id: int) -> dict:
    row = db.execute(
        select(Puesto, Seccion, Departamento, Area, Empresa)
        .join(Seccion, Seccion.id == Puesto.seccion_id)
        .join(Departamento, Departamento.id == Seccion.departamento_id)
        .join(Area, Area.id == Departamento.area_id)
        .join(Empresa, Empresa.id == Area.empresa_id)
        .where(Puesto.id == puesto_id)
    ).one()
    p, s, d, a, e = row
    return {
        "puesto": {"id": p.id, "codigo": p.codigo, "nombre": p.nombre},
        "seccion": {"id": s.id, "codigo": s.codigo, "nombre": s.nombre},
        "departamento": {"id": d.id, "codigo": d.codigo, "nombre": d.nombre},
        "area": {"id": a.id, "codigo": a.codigo, "nombre": a.nombre},
        "empresa": {"id": e.id, "codigo": e.codigo, "nombre": e.nombre},
    }


def _guardar_roles(db: Session, u: Usuario, data: dict):
    if "roles_adicionales" not in data:
        return
    roles = set(data.get("roles_adicionales") or [])
    if not roles <= set(ROLES):
        raise NegocioError(f"Roles inválidos. Use {ROLES}.")
    db.execute(text("DELETE FROM usuario_rol WHERE usuario_id=:u"), {"u": u.id})
    for r in roles - {u.rol}:
        db.execute(text("INSERT INTO usuario_rol (usuario_id, rol) VALUES (:u, :r)"), {"u": u.id, "r": r})


def usuario_dict(db: Session, u: Usuario) -> dict:
    d = a_dict(u)
    d["roles"] = sorted({u.rol} | {r for (r,) in db.execute(text("SELECT rol FROM usuario_rol WHERE usuario_id=:u"),
                                                                {"u": u.id})})
    d["organizacion"] = ruta_organizacional(db, u.puesto_id)
    return d


def _validar_password(pw) -> str:
    if not isinstance(pw, str) or len(pw) < 8:
        raise NegocioError("La contraseña debe tener al menos 8 caracteres.")
    return pw


def _validar_usuario_unico(db: Session, usuario: str, correo: str | None, excluir_id: int | None = None):
    q = select(Usuario.id).where(func.lower(Usuario.usuario) == usuario.lower())
    if excluir_id:
        q = q.where(Usuario.id != excluir_id)
    if db.scalar(q):
        raise NegocioError(f"El nombre de usuario '{usuario}' ya está registrado.", 409)
    if correo:
        q = select(Usuario.id).where(func.lower(Usuario.correo) == correo.lower())
        if excluir_id:
            q = q.where(Usuario.id != excluir_id)
        if db.scalar(q):
            raise NegocioError(f"El correo '{correo}' ya está registrado.", 409)


def _validar_puesto(db: Session, puesto_id) -> int:
    _padre_activo(db, Spec(Usuario, "usuario", "puesto_id", Puesto, "puesto"), puesto_id)
    return int(puesto_id)


def crear_usuario(db: Session, data: dict) -> dict:
    nombre = _texto(data, "nombre")
    usuario = _texto(data, "usuario", maximo=80)
    correo = _texto(data, "correo", requerido=False)
    if correo and "@" not in correo:
        raise NegocioError("El correo no tiene un formato válido.")
    rol = data.get("rol")
    if rol not in ROLES:
        raise NegocioError(f"El rol debe ser uno de {ROLES}.")
    pw = _validar_password(data.get("password"))
    puesto_id = _validar_puesto(db, data.get("puesto_id"))
    _validar_usuario_unico(db, usuario, correo)
    u = Usuario(nombre=nombre, usuario=usuario, correo=correo, rol=rol, puesto_id=puesto_id,
                password_hash=hash_password(pw), activo=True)
    db.add(u)
    db.flush()
    _guardar_roles(db, u, data)
    db.commit()
    return usuario_dict(db, u)


def actualizar_usuario(db: Session, u: Usuario, data: dict) -> dict:
    nombre = _texto(data, "nombre", requerido=False) or u.nombre
    usuario = _texto(data, "usuario", requerido=False, maximo=80) or u.usuario
    correo = _texto(data, "correo", requerido=False) if "correo" in data else u.correo
    rol = data.get("rol") or u.rol
    if rol not in ROLES:
        raise NegocioError(f"El rol debe ser uno de {ROLES}.")
    if data.get("puesto_id") not in (None, "") and int(data["puesto_id"]) != u.puesto_id:
        u.puesto_id = _validar_puesto(db, data["puesto_id"])
    _validar_usuario_unico(db, usuario, correo, excluir_id=u.id)
    if data.get("password"):
        u.password_hash = hash_password(_validar_password(data["password"]))
        revocar_sesiones_usuario(db, u.id)
    u.nombre, u.usuario, u.correo, u.rol = nombre, usuario, correo, rol
    _guardar_roles(db, u, data)
    db.commit()
    return usuario_dict(db, u)
