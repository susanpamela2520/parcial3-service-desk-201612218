"""Operaciones de tickets: creación, toma, asignación, transferencia, escalamiento,
transiciones de los siete procesos, comentarios, adjuntos, CI, relaciones, reapertura y eventos.

Concurrencia: las operaciones críticas bloquean la fila del ticket (SELECT … FOR UPDATE) y la
toma usa un UPDATE condicional (responsable_id IS NULL), de modo que si dos agentes intentan
tomar el mismo ticket solo uno obtiene la asignación.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..models import Usuario
from . import permisos as perm
from .errores import NegocioError
from .procesos import (FLUJOS, MATRIZ_PRIORIDAD, PRIORIDAD_INCIDENTE, PRIORIDAD_NUM, REDUNDANCIA_INCIDENTE,
                       RESPUESTA_EVENTO, categoria_por_duracion)

COLUMNAS_DATO = {"categoria_incidente", "afectacion", "duracion_estimada_min", "duracion_real_min",
                 "redundancia_disponible", "categoria_evento", "beneficiario_id", "responsable_id"}
CANALES = {"PORTAL", "TELEFONO", "CORREO", "CHAT", "MONITOREO"}
TIPOS_ADJUNTO = {"application/pdf", "image/png", "image/jpeg", "text/plain"}
MAX_ADJUNTO = 2 * 1024 * 1024
DIAS_REAPERTURA = 7
MAX_REAPERTURAS = 3
COLUMNAS_TICKET = ("id, numero, titulo, descripcion, solicitante_id, creador_id, servicio_n2_id, tipo_ticket, "
                   "proceso_codigo, cola_id, responsable_id, estado, prioridad, impacto, urgencia, canal, nivel_soporte, "
                   "categoria_evento, huella_evento, categoria_incidente, afectacion, duracion_estimada_min, "
                   "duracion_real_min, redundancia_disponible, beneficiario_id, aprobado_por_id, aprobado_en, datos, "
                   "sin_ci_motivo, reaperturas, version, lote, creado_en, actualizado_en, primera_atencion_en, "
                   "resuelto_en, cerrado_en")


def ahora():
    return datetime.now(timezone.utc)


def _json(v):
    return json.dumps(v, default=str, ensure_ascii=False)


def obtener(db: Session, tid: int, bloquear: bool = False) -> dict:
    fila = db.execute(text(f"SELECT {COLUMNAS_TICKET} FROM ticket WHERE id=:id" + (" FOR UPDATE" if bloquear else "")),
                      {"id": tid}).mappings().first()
    if fila is None:
        raise NegocioError(f"No existe el ticket con id {tid}.", 404)
    return dict(fila)


def registrar(db: Session, tid: int, actor: int, accion: str, anterior=None, nuevo=None, motivo=None):
    db.execute(text("INSERT INTO ticket_bitacora (ticket_id, actor_id, accion, anterior, nuevo, motivo) "
                    "VALUES (:t, :a, :ac, CAST(:an AS jsonb), CAST(:nu AS jsonb), :m)"),
               {"t": tid, "a": actor, "ac": accion, "an": _json(anterior) if anterior is not None else None,
                "nu": _json(nuevo) if nuevo is not None else None, "m": motivo})


def notificar(db: Session, mensaje: str, ticket_id: int | None = None, usuario_id: int | None = None,
              cola_id: int | None = None):
    db.execute(text("INSERT INTO notificacion (usuario_id, cola_id, ticket_id, mensaje) VALUES (:u, :c, :t, :m)"),
               {"u": usuario_id, "c": cola_id, "t": ticket_id, "m": mensaje})


def _cola(db: Session, cola_id) -> dict:
    c = db.execute(text("SELECT * FROM cola WHERE id=:id"), {"id": int(cola_id)}).mappings().first()
    if c is None:
        raise NegocioError(f"La cola indicada (id {cola_id}) no existe.", 404)
    return dict(c)


def cola_por_defecto(db: Session, proceso: str) -> dict:
    c = db.execute(text("SELECT * FROM cola WHERE proceso_codigo=:p AND activo ORDER BY nivel_soporte, id LIMIT 1"),
                   {"p": proceso}).mappings().first()
    if c is None:
        raise NegocioError(f"No hay una cola activa para el proceso {proceso}.", 409)
    return dict(c)


def calcular_prioridad(impacto: str, urgencia: str, categoria_incidente: str | None = None) -> int:
    p = MATRIZ_PRIORIDAD[(impacto, urgencia)]
    if categoria_incidente:  # la agrupación de la diapositiva 16 fija un mínimo de prioridad
        p = min(p, PRIORIDAD_NUM[PRIORIDAD_INCIDENTE[categoria_incidente]])
    return p


# ------------------------------------------------------------------------ creación
def crear(db: Session, u: Usuario, data: dict, commit: bool = True) -> dict:
    roles = perm.exigir_escritura(db, u)
    titulo = (data.get("titulo") or "").strip()
    descripcion = (data.get("descripcion") or "").strip()
    if not titulo or not descripcion:
        raise NegocioError("Título y descripción son obligatorios.")
    proceso = data.get("proceso_codigo")
    if proceso not in FLUJOS:
        raise NegocioError(f"Proceso inválido: {proceso}. Use uno de {sorted(FLUJOS)}.")
    srv = db.execute(text("SELECT id, dado_de_baja FROM servicio_n2 WHERE id=:id"),
                     {"id": data.get("servicio_n2_id") or 0}).first()
    if srv is None:
        raise NegocioError("El servicio de nivel 2 indicado no existe.", 404)
    if srv.dado_de_baja and proceso == "SERVICIO_TI":
        raise NegocioError("El servicio indicado está dado de baja.", 409)
    impacto, urgencia = data.get("impacto", "MEDIO"), data.get("urgencia", "MEDIA")
    if (impacto, urgencia) not in MATRIZ_PRIORIDAD:
        raise NegocioError("Impacto debe ser ALTO/MEDIO/BAJO y urgencia ALTA/MEDIA/BAJA.")
    canal = data.get("canal", "PORTAL")
    if canal not in CANALES:
        raise NegocioError(f"Canal inválido. Use uno de {sorted(CANALES)}.")
    solicitante_id = int(data.get("solicitante_id") or u.id)
    if solicitante_id != u.id and not (roles & perm.ROLES_OPERATIVOS):
        raise NegocioError("Solo un agente puede registrar tickets a nombre de otra persona.", 403)
    sol = db.execute(text("SELECT activo FROM usuario WHERE id=:id"), {"id": solicitante_id}).first()
    if sol is None or not sol.activo:
        raise NegocioError("El solicitante no existe o está inactivo.", 422)
    cola = _cola(db, data["cola_id"]) if data.get("cola_id") else cola_por_defecto(db, proceso)
    if cola["proceso_codigo"] != proceso or not cola["activo"]:
        raise NegocioError("La cola indicada no está activa o no corresponde al proceso.", 422)
    ci_ids = [int(x) for x in (data.get("ci_ids") or [])]
    sin_ci = (data.get("sin_ci_motivo") or "").strip() or None
    if ci_ids:
        encontrados = db.execute(text("SELECT count(*) FROM ci WHERE id = ANY(:ids) AND activo"), {"ids": ci_ids}).scalar()
        if encontrados != len(set(ci_ids)):
            raise NegocioError("Uno o más CI indicados no existen o están inactivos.", 404)
        sin_ci = None
    elif proceso in ("INCIDENTE", "CAMBIO", "EVENTO") and not sin_ci:
        raise NegocioError("Indique los CI afectados o el motivo por el que el ticket no tiene CI asociado.")
    elif not sin_ci:
        sin_ci = f"No aplica CI para el proceso {proceso}"
    cat_inc = data.get("categoria_incidente") if proceso == "INCIDENTE" else None
    datos = dict(data.get("datos") or {})
    huella = data.get("huella_evento") if proceso == "EVENTO" else None
    beneficiario = data.get("beneficiario_id") if proceso == "ACCESO" else None
    if proceso == "ACCESO" and not beneficiario:
        raise NegocioError("La solicitud de acceso requiere un usuario beneficiario.")
    numero = "TK-%06d" % db.execute(text("SELECT nextval('ticket_numero_seq')")).scalar()
    t = db.execute(text("""
        INSERT INTO ticket (numero, titulo, descripcion, solicitante_id, creador_id, servicio_n2_id, tipo_ticket,
            proceso_codigo, cola_id, estado, prioridad, impacto, urgencia, canal, nivel_soporte, categoria_evento,
            huella_evento, categoria_incidente, afectacion, duracion_estimada_min, beneficiario_id, datos,
            sin_ci_motivo, lote, creado_en, actualizado_en)
        SELECT :numero, :titulo, :desc, :sol, :creador, :srv, p.tipo_ticket, p.codigo, :cola, :estado, :prio, :imp,
            :urg, :canal, :nivel, :cat_ev, :huella, :cat_inc, :afect, :dur, :benef, CAST(:datos AS jsonb), :sin_ci,
            :lote, COALESCE(CAST(:creado AS timestamptz), now()), COALESCE(CAST(:creado AS timestamptz), now())
        FROM proceso p WHERE p.codigo = :proc
        RETURNING id"""), {
        "numero": numero, "titulo": titulo[:200], "desc": descripcion, "sol": solicitante_id, "creador": u.id,
        "srv": srv.id, "cola": cola["id"], "estado": FLUJOS[proceso].inicial,
        "prio": calcular_prioridad(impacto, urgencia, cat_inc), "imp": impacto, "urg": urgencia, "canal": canal,
        "nivel": cola["nivel_soporte"], "cat_ev": data.get("categoria_evento") if proceso == "EVENTO" else None,
        "huella": huella, "cat_inc": cat_inc, "afect": data.get("afectacion") if proceso == "INCIDENTE" else None,
        "dur": data.get("duracion_estimada_min"), "benef": beneficiario, "datos": _json(datos), "sin_ci": sin_ci,
        "lote": data.get("lote", "MANUAL"), "creado": data.get("creado_en"), "proc": proceso}).scalar()
    for ci_id in set(ci_ids):
        db.execute(text("INSERT INTO ticket_ci (ticket_id, ci_id) VALUES (:t, :c)"), {"t": t, "c": ci_id})
    registrar(db, t, u.id, "CREACION", None, {"numero": numero, "proceso": proceso, "cola_id": cola["id"],
                                              "estado": FLUJOS[proceso].inicial, "ci_ids": sorted(set(ci_ids)),
                                              "sin_ci_motivo": sin_ci})
    if commit:
        db.commit()
    return detalle(db, u, t)


# ------------------------------------------------------------------------ consulta
def detalle(db: Session, u: Usuario, tid: int) -> dict:
    t = obtener(db, tid)
    roles = perm.roles_de(db, u)
    if not perm.puede_ver_ticket(db, u, t, roles):
        raise NegocioError("No tiene acceso a este ticket.", 403)
    p = perm.permisos_cola(db, u, t["cola_id"], roles)
    ve_interno = bool(roles & {"administrador", "consulta"}) or p.consultar
    q = lambda sql, **kw: [dict(r) for r in db.execute(text(sql), {"t": tid, **kw}).mappings()]  # noqa: E731
    t["servicio"] = q("""SELECT s.id, s.codigo, s.nombre, n.id AS n1_id, n.codigo AS n1_codigo, n.nombre AS n1_nombre
                         FROM servicio_n2 s JOIN servicio_n1 n ON n.id=s.servicio_n1_id
                         WHERE s.id=(SELECT servicio_n2_id FROM ticket WHERE id=:t)""")[0]
    t["cola"] = q("SELECT c.id, c.codigo, c.nombre, c.nivel_soporte FROM cola c WHERE c.id=(SELECT cola_id FROM ticket WHERE id=:t)")[0]
    t["cis"] = q("SELECT c.id, c.codigo, c.nombre FROM ticket_ci tc JOIN ci c ON c.id=tc.ci_id WHERE tc.ticket_id=:t ORDER BY c.codigo")
    t["relaciones"] = q("""SELECT r.tipo, r.origen_id, o.numero AS origen_numero, r.destino_id, d.numero AS destino_numero
                           FROM ticket_relacion r JOIN ticket o ON o.id=r.origen_id JOIN ticket d ON d.id=r.destino_id
                           WHERE r.origen_id=:t OR r.destino_id=:t ORDER BY r.id""")
    t["comentarios"] = q("""SELECT c.id, c.texto, c.interno, c.fecha, u.nombre AS autor FROM ticket_comentario c
                            JOIN usuario u ON u.id=c.autor_id WHERE c.ticket_id=:t """
                         + ("" if ve_interno else "AND NOT c.interno ") + "ORDER BY c.id")
    t["adjuntos"] = q("SELECT id, nombre, tipo_mime, tamano, interno, fecha FROM ticket_adjunto WHERE ticket_id=:t "
                      + ("" if ve_interno else "AND NOT interno ") + "ORDER BY id")
    t["bitacora"] = q("""SELECT b.id, b.fecha, b.accion, b.anterior, b.nuevo, b.motivo, u.nombre AS actor
                         FROM ticket_bitacora b JOIN usuario u ON u.id=b.actor_id WHERE b.ticket_id=:t ORDER BY b.id""") \
        if ve_interno else []
    t["transiciones"] = [{"hacia": x.hacia, "permiso": x.permiso, "requiere": list(x.requiere)}
                         for x in FLUJOS[t["proceso_codigo"]].desde(t["estado"])]
    t["permisos"] = p.__dict__
    return t


def buscar(db: Session, u: Usuario, f: dict) -> dict:
    roles = perm.roles_de(db, u)
    cond, params = [], {}
    if not roles & {"administrador", "consulta"}:
        params["u"], params["roles"] = u.id, list(roles)
        cond.append("""(t.solicitante_id=:u OR t.creador_id=:u OR t.beneficiario_id=:u OR t.cola_id IN (
            SELECT m.cola_id FROM cola_miembro m WHERE m.activo AND m.puede_consultar
              AND (m.usuario_id=:u OR m.rol = ANY(:roles))))""")
    if f.get("mios"):
        params["yo"] = u.id
        cond.append("t.solicitante_id=:yo")
    filtros = {"numero": "t.numero ILIKE :numero", "servicio_n2_id": "t.servicio_n2_id=:servicio_n2_id",
               "proceso": "t.proceso_codigo=:proceso", "cola_id": "t.cola_id=:cola_id", "estado": "t.estado=:estado",
               "prioridad": "t.prioridad=:prioridad", "responsable_id": "t.responsable_id=:responsable_id",
               "desde": "t.creado_en >= CAST(:desde AS timestamptz)", "hasta": "t.creado_en < CAST(:hasta AS timestamptz)"}
    for k, sql in filtros.items():
        v = f.get(k)
        if v not in (None, ""):
            params[k] = f"%{v}%" if k == "numero" else v
            cond.append(sql)
    if f.get("sin_asignar"):
        cond.append("t.responsable_id IS NULL")
    if f.get("abiertos"):
        cond.append("t.estado <> 'CERRADO'")
    where = ("WHERE " + " AND ".join(cond)) if cond else ""
    tam = max(1, min(int(f.get("tamano") or 20), 100))
    pag = max(1, int(f.get("pagina") or 1))
    total = db.execute(text(f"SELECT count(*) FROM ticket t {where}"), params).scalar()
    filas = db.execute(text(f"""
        SELECT t.id, t.numero, t.titulo, t.proceso_codigo, t.estado, t.prioridad, t.creado_en, t.cola_id,
               c.codigo AS cola, s.codigo AS servicio, r.nombre AS responsable, t.categoria_evento, t.categoria_incidente
        FROM ticket t JOIN cola c ON c.id=t.cola_id JOIN servicio_n2 s ON s.id=t.servicio_n2_id
        LEFT JOIN usuario r ON r.id=t.responsable_id {where}
        ORDER BY t.prioridad, t.creado_en, t.id LIMIT :lim OFFSET :off"""),
        {**params, "lim": tam, "off": (pag - 1) * tam}).mappings()
    return {"total": total, "pagina": pag, "tamano": tam, "paginas": max(1, -(-total // tam)),
            "items": [dict(x) for x in filas]}


# ------------------------------------------------------------------------ asignación
def _marcar_primera_atencion(db: Session, tid: int):
    db.execute(text("UPDATE ticket SET primera_atencion_en = COALESCE(primera_atencion_en, now()) WHERE id=:id"), {"id": tid})


def tomar(db: Session, u: Usuario, tid: int) -> dict:
    t = obtener(db, tid)
    if not perm.permisos_cola(db, u, t["cola_id"]).atender:
        raise NegocioError("No tiene permiso para atender tickets de esta cola.", 403)
    r = db.execute(text("""UPDATE ticket SET responsable_id=:u, version=version+1, actualizado_en=now(),
                              primera_atencion_en = COALESCE(primera_atencion_en, now())
                           WHERE id=:id AND responsable_id IS NULL AND estado <> 'CERRADO' RETURNING id"""),
                   {"u": u.id, "id": tid}).first()
    if r is None:
        db.rollback()
        raise NegocioError("El ticket ya fue tomado por otro agente o está cerrado.", 409)
    registrar(db, tid, u.id, "TOMA", {"responsable_id": None}, {"responsable_id": u.id})
    db.commit()
    return obtener(db, tid)


def tomar_siguiente(db: Session, u: Usuario, cola_id: int) -> dict:
    if not perm.permisos_cola(db, u, cola_id).atender:
        raise NegocioError("No tiene permiso para atender tickets de esta cola.", 403)
    cola = _cola(db, cola_id)
    orden = "t.prioridad, t.creado_en, t.id" if cola["regla_orden"] == "PRIORIDAD_ANTIGUEDAD" else "t.creado_en, t.id"
    fila = db.execute(text(f"""SELECT t.id FROM ticket t WHERE t.cola_id=:c AND t.responsable_id IS NULL
                               AND t.estado <> 'CERRADO' ORDER BY {orden} LIMIT 1 FOR UPDATE SKIP LOCKED"""),
                      {"c": cola_id}).first()
    if fila is None:
        db.rollback()
        raise NegocioError("No hay tickets pendientes en la cola.", 404)
    db.execute(text("""UPDATE ticket SET responsable_id=:u, version=version+1, actualizado_en=now(),
                       primera_atencion_en=COALESCE(primera_atencion_en, now()) WHERE id=:id"""), {"u": u.id, "id": fila.id})
    registrar(db, fila.id, u.id, "TOMA", {"responsable_id": None}, {"responsable_id": u.id}, "Siguiente de la cola")
    db.commit()
    return obtener(db, fila.id)


def siguientes(db: Session, cola_id: int, limite: int = 20) -> list[dict]:
    cola = _cola(db, cola_id)
    orden = "prioridad, creado_en, id" if cola["regla_orden"] == "PRIORIDAD_ANTIGUEDAD" else "creado_en, id"
    return [dict(x) for x in db.execute(text(f"""SELECT id, numero, prioridad, creado_en FROM ticket WHERE cola_id=:c
        AND responsable_id IS NULL AND estado <> 'CERRADO' ORDER BY {orden} LIMIT :l"""), {"c": cola_id, "l": limite}).mappings()]


def _agente_autorizado(db: Session, usuario_id: int, cola_id: int) -> Usuario:
    destino = db.get(Usuario, int(usuario_id))
    if destino is None or not destino.activo:
        raise NegocioError("El usuario destino no existe o está inactivo.", 422)
    if not perm.permisos_cola(db, destino, cola_id).atender:
        raise NegocioError(f"El usuario '{destino.usuario}' no está autorizado para atender esta cola.", 422)
    return destino


def asignar(db: Session, u: Usuario, tid: int, usuario_id: int, motivo: str | None = None) -> dict:
    t = obtener(db, tid, bloquear=True)
    p = perm.permisos_cola(db, u, t["cola_id"])
    if not (p.transferir or p.administrar):
        raise NegocioError("No tiene permiso para asignar tickets en esta cola.", 403)
    if t["estado"] == "CERRADO":
        raise NegocioError("No se puede asignar un ticket cerrado.", 409)
    destino = _agente_autorizado(db, usuario_id, t["cola_id"])
    db.execute(text("UPDATE ticket SET responsable_id=:r, version=version+1, actualizado_en=now() WHERE id=:id"),
               {"r": destino.id, "id": tid})
    _marcar_primera_atencion(db, tid)
    registrar(db, tid, u.id, "ASIGNACION", {"responsable_id": t["responsable_id"]}, {"responsable_id": destino.id}, motivo)
    notificar(db, f"Se le asignó el ticket {t['numero']}", tid, usuario_id=destino.id)
    db.commit()
    return obtener(db, tid)


def transferir(db: Session, u: Usuario, tid: int, cola_destino: int, motivo: str, escalar: bool = False) -> dict:
    if not (motivo or "").strip():
        raise NegocioError("Debe indicar el motivo de la transferencia.")
    t = obtener(db, tid, bloquear=True)
    if not perm.permisos_cola(db, u, t["cola_id"]).transferir:
        raise NegocioError("No tiene permiso para transferir tickets de esta cola.", 403)
    if t["estado"] == "CERRADO":
        raise NegocioError("No se puede transferir un ticket cerrado.", 409)
    destino = _cola(db, cola_destino)
    if not destino["activo"]:
        raise NegocioError("La cola destino está inactiva.", 409)
    if destino["id"] == t["cola_id"]:
        raise NegocioError("La cola destino es la misma cola actual.", 422)
    if destino["proceso_codigo"] != t["proceso_codigo"]:
        raise NegocioError(f"La cola destino pertenece al proceso {destino['proceso_codigo']}; "
                           f"el ticket es del proceso {t['proceso_codigo']}.", 422)
    if escalar and destino["nivel_soporte"] <= t["nivel_soporte"]:
        raise NegocioError("Para escalar, la cola destino debe tener un nivel de soporte mayor.", 422)
    nuevo_estado = t["estado"]
    if escalar and t["proceso_codigo"] == "INCIDENTE" and t["estado"] == "EN_ATENCION":
        nuevo_estado = "ESCALADO"
    db.execute(text("""UPDATE ticket SET cola_id=:c, nivel_soporte=:n, responsable_id=NULL, estado=:e,
                       version=version+1, actualizado_en=now() WHERE id=:id"""),
               {"c": destino["id"], "n": destino["nivel_soporte"], "e": nuevo_estado, "id": tid})
    registrar(db, tid, u.id, "ESCALAMIENTO" if escalar else "TRANSFERENCIA",
              {"cola_id": t["cola_id"], "nivel_soporte": t["nivel_soporte"], "responsable_id": t["responsable_id"],
               "estado": t["estado"]},
              {"cola_id": destino["id"], "nivel_soporte": destino["nivel_soporte"], "responsable_id": None,
               "estado": nuevo_estado}, motivo)
    notificar(db, f"Ticket {t['numero']} {'escalado' if escalar else 'transferido'} a {destino['codigo']}", tid,
              cola_id=destino["id"])
    db.commit()
    return obtener(db, tid)


# ------------------------------------------------------------------------ transiciones
def _valor(db: Session, t: dict, campo: str):
    if campo == "ci_afectados":
        return db.execute(text("SELECT count(*) FROM ticket_ci WHERE ticket_id=:t"), {"t": t["id"]}).scalar() or None
    if campo == "incidentes_vinculados":
        return db.execute(text("SELECT count(*) FROM ticket_relacion WHERE destino_id=:t AND tipo='INCIDENTE_DE_PROBLEMA'"),
                          {"t": t["id"]}).scalar() or None
    if campo in t:
        return t[campo]
    return (t.get("datos") or {}).get(campo)


def _incidente_de_evento(db: Session, evento_id: int):
    return db.execute(text("SELECT destino_id FROM ticket_relacion WHERE origen_id=:e AND tipo='EVENTO_GENERA_INCIDENTE'"),
                      {"e": evento_id}).scalar()


def _efecto_evento(db: Session, u: Usuario, t: dict) -> int:
    """E2/E3: crea (o reutiliza) el incidente vinculado y registra una notificación verificable."""
    existente = _incidente_de_evento(db, t["id"])
    if existente:
        return existente
    cola = cola_por_defecto(db, "INCIDENTE")
    cis = [c for (c,) in db.execute(text("SELECT ci_id FROM ticket_ci WHERE ticket_id=:t"), {"t": t["id"]})]
    numero = "TK-%06d" % db.execute(text("SELECT nextval('ticket_numero_seq')")).scalar()
    afect = "SEGURIDAD" if t["categoria_evento"] == "E3" else "INTERRUPCION"
    inc = db.execute(text("""
        INSERT INTO ticket (numero, titulo, descripcion, solicitante_id, creador_id, servicio_n2_id, tipo_ticket,
            proceso_codigo, cola_id, estado, prioridad, impacto, urgencia, canal, nivel_soporte, afectacion,
            sin_ci_motivo, datos, lote)
        VALUES (:n, :ti, :de, :sol, :cr, :srv, 'INCIDENTE', 'INCIDENTE', :cola, 'REGISTRADO', 2, 'ALTO', 'ALTA',
            'MONITOREO', :niv, :af, :sinci, CAST(:datos AS jsonb), :lote) RETURNING id"""), {
        "n": numero, "ti": f"Incidente generado por evento {t['numero']} ({t['categoria_evento']})"[:200],
        "de": t["descripcion"], "sol": t["solicitante_id"], "cr": u.id, "srv": t["servicio_n2_id"], "cola": cola["id"],
        "niv": cola["nivel_soporte"], "af": afect, "sinci": None if cis else "El evento origen no tenía CI asociado",
        "datos": _json({"evento_origen": t["numero"]}), "lote": t["lote"]}).scalar()
    for c in cis:
        db.execute(text("INSERT INTO ticket_ci VALUES (:t, :c)"), {"t": inc, "c": c})
    db.execute(text("INSERT INTO ticket_relacion (origen_id, destino_id, tipo, creado_por) "
                    "VALUES (:o, :d, 'EVENTO_GENERA_INCIDENTE', :u)"), {"o": t["id"], "d": inc, "u": u.id})
    registrar(db, inc, u.id, "CREACION", None, {"numero": numero, "origen_evento": t["numero"]})
    registrar(db, t["id"], u.id, "INCIDENTE_GENERADO", None, {"incidente_id": inc, "numero": numero})
    notificar(db, f"[{t['categoria_evento']}] Evento {t['numero']} generó el incidente {numero}", inc, cola_id=cola["id"])
    notificar(db, f"Su evento {t['numero']} generó el incidente {numero}", inc, usuario_id=t["solicitante_id"])
    return inc


def _efecto_nuevo_servicio(db: Session, t: dict, datos: dict) -> int:
    """Crea o vincula el servicio del catálogo SIN duplicar códigos."""
    prop = datos.get("servicio_propuesto") or (t.get("datos") or {}).get("servicio_propuesto") or {}
    if prop.get("vincular_id"):
        sid = db.execute(text("SELECT id FROM servicio_n2 WHERE id=:i"), {"i": int(prop["vincular_id"])}).scalar()
        if sid is None:
            raise NegocioError("El servicio a vincular no existe.", 404)
        return sid
    codigo = (prop.get("codigo") or "").strip().upper()
    if not codigo or not prop.get("nombre") or not prop.get("servicio_n1_id"):
        raise NegocioError("servicio_propuesto requiere vincular_id, o bien codigo, nombre y servicio_n1_id.")
    existente = db.execute(text("SELECT id FROM servicio_n2 WHERE upper(codigo)=:c"), {"c": codigo}).scalar()
    if existente:
        return existente  # ya existe: se vincula, no se duplica
    return db.execute(text("""INSERT INTO servicio_n2 (servicio_n1_id, codigo, codigo_original, nombre, estado_revision,
        origen_hoja) VALUES (:n1, :c, :c, :n, 'REVISION', :o) RETURNING id"""),
        {"n1": int(prop["servicio_n1_id"]), "c": codigo, "n": prop["nombre"], "o": f"Ticket {t['numero']}"}).scalar()


def transicionar(db: Session, u: Usuario, tid: int, hacia: str, datos: dict | None = None,
                 motivo: str | None = None, commit: bool = True) -> dict:
    datos = dict(datos or {})
    roles = perm.exigir_escritura(db, u)
    t = obtener(db, tid, bloquear=True)
    flujo = FLUJOS[t["proceso_codigo"]]
    tr = flujo.siguiente(t["estado"], hacia)
    if tr is None:
        raise NegocioError(f"Transición inválida en {t['proceso_codigo']}: {t['estado']} → {hacia}. Permitidas: "
                           f"{[x.hacia for x in flujo.desde(t['estado'])] or 'ninguna'}.", 409)
    p = perm.permisos_cola(db, u, t["cola_id"], roles)
    if tr.permiso == "aprobar":
        if not p.aprobar:
            raise NegocioError("Se requiere permiso de aprobación en esta cola (rol aprobador).", 403)
        if u.id == t["solicitante_id"]:
            raise NegocioError("El solicitante no puede aprobar su propia solicitud.", 403)
        if t["proceso_codigo"] == "ACCESO" and u.id == (datos.get("beneficiario_id") or t["beneficiario_id"]):
            raise NegocioError("El beneficiario no puede aprobar su propio acceso.", 403)
    elif tr.permiso == "solicitante":
        if u.id != t["solicitante_id"] and not p.atender:
            raise NegocioError("Solo el solicitante o un agente de la cola puede realizar este paso.", 403)
    else:
        if not p.atender:
            raise NegocioError("No tiene permiso para atender tickets de esta cola.", 403)
        if t["responsable_id"] not in (None, u.id) and not p.administrar:
            raise NegocioError("El ticket está asignado a otro agente.", 409)

    cambios_col = {k: datos.pop(k) for k in list(datos) if k in COLUMNAS_DATO}
    nuevo = {**t, **cambios_col}
    nuevo_datos = {**(t["datos"] or {}), **datos}
    nuevo["datos"] = nuevo_datos
    proc, cat_ev = t["proceso_codigo"], nuevo.get("categoria_evento")

    # ---- validaciones propias de cada proceso
    if proc == "INCIDENTE" and hacia == "CLASIFICADO":
        cat = nuevo.get("categoria_incidente")
        if cat not in PRIORIDAD_INCIDENTE:
            raise NegocioError("categoria_incidente debe ser I0–I6.")
        if nuevo.get("afectacion") not in ("NINGUNA", "DEGRADACION", "INTERRUPCION", "SEGURIDAD"):
            raise NegocioError("afectacion debe ser NINGUNA, DEGRADACION, INTERRUPCION o SEGURIDAD.")
        sugerida = categoria_por_duracion(nuevo["afectacion"], nuevo.get("duracion_estimada_min"))
        if sugerida and sugerida != cat and not nuevo_datos.get("justificacion_categoria"):
            raise NegocioError(f"La categoría {cat} no coincide con la regla complementaria (sugerida {sugerida}). "
                               "Corrija la categoría o registre justificacion_categoria.", 422)
        red = nuevo.get("redundancia_disponible")
        if red is None:
            nuevo["redundancia_disponible"] = REDUNDANCIA_INCIDENTE[cat]
        elif red != REDUNDANCIA_INCIDENTE[cat] and not nuevo_datos.get("justificacion_categoria"):
            raise NegocioError(f"La redundancia indicada no corresponde a la agrupación de {cat} (diapositiva 16).", 422)
        nuevo["prioridad"] = calcular_prioridad(t["impacto"], t["urgencia"], cat)
    if proc == "EVENTO":
        if hacia == "CLASIFICADO" and cat_ev not in RESPUESTA_EVENTO:
            raise NegocioError("categoria_evento debe ser E0–E3.")
        if t["estado"] == "CLASIFICADO" and hacia == "CERRADO" and cat_ev != "E0":
            raise NegocioError("Solo un evento E0 se cierra con el registro únicamente; evalúelo primero.", 409)
        if t["estado"] == "EVALUADO" and hacia == "CERRADO" and cat_ev != "E1":
            raise NegocioError(f"Un evento {cat_ev} requiere respuesta (incidente y notificación) antes del cierre.", 409)
        if hacia == "RESPONDIDO" and cat_ev not in ("E2", "E3"):
            raise NegocioError("Solo E2 y E3 requieren respuesta con incidente.", 409)
    if proc == "ACCESO":
        if hacia == "APROBADO":
            nuevo_datos.setdefault("recurso", nuevo_datos.get("recurso"))
            if nuevo_datos.get("accion_acceso") not in ("PROVISION", "REVOCACION"):
                raise NegocioError("accion_acceso debe ser PROVISION o REVOCACION.")
        if hacia == "PROVISIONADO" and nuevo_datos.get("accion_acceso") != "PROVISION":
            raise NegocioError("La solicitud es de revocación; use el estado REVOCADO.", 409)
        if hacia == "REVOCADO" and nuevo_datos.get("accion_acceso") != "REVOCACION":
            raise NegocioError("La solicitud es de provisión; use el estado PROVISIONADO.", 409)
    if proc == "SERVICIO_TI" and hacia == "ASIGNADO":
        rid = nuevo.get("responsable_id")
        if rid:
            _agente_autorizado(db, rid, t["cola_id"])
    if proc == "NUEVO_SERVICIO" and hacia == "IMPLEMENTADO":
        nuevo_datos["servicio_resultante_id"] = _efecto_nuevo_servicio(db, t, nuevo_datos)
    if hacia in ("APROBADO",):
        nuevo["aprobado_por_id"], nuevo["aprobado_en"] = u.id, ahora()
    if proc == "CAMBIO" and hacia == "EN_EJECUCION" and not t["aprobado_por_id"]:
        raise NegocioError("No se puede ejecutar un cambio sin aprobación.", 409)

    faltan = [c for c in tr.requiere if _valor(db, nuevo, c) in (None, "", [], {})]
    if faltan:
        raise NegocioError(f"Faltan datos para pasar a {hacia}: {', '.join(faltan)}.", 422)

    if proc == "EVENTO" and hacia == "RESPONDIDO":
        nuevo_datos["incidente_id"] = _efecto_evento(db, u, nuevo)

    # ---- persistencia
    momento = ahora()
    set_ = {"estado": hacia, "datos": _json(nuevo_datos)}
    for k in ("categoria_incidente", "afectacion", "duracion_estimada_min", "duracion_real_min", "redundancia_disponible",
              "categoria_evento", "beneficiario_id", "responsable_id", "prioridad", "aprobado_por_id", "aprobado_en"):
        if nuevo.get(k) != t.get(k):
            set_[k] = nuevo.get(k)
    if hacia in flujo.resuelto:
        set_["resuelto_en"] = momento
    if hacia == "CERRADO":
        set_["cerrado_en"] = momento
        if not t["resuelto_en"] and "resuelto_en" not in set_:
            # Loop 1: E0/E1 se cierran sin estado de respuesta; el cierre es su resolución
            set_["resuelto_en"] = momento
    asignaciones = ", ".join(f"{k} = CAST(:{k} AS jsonb)" if k == "datos" else f"{k} = :{k}" for k in set_)
    db.execute(text(f"UPDATE ticket SET {asignaciones}, version=version+1, actualizado_en=now() WHERE id=:id"),
               {**set_, "id": tid})
    if u.id != t["solicitante_id"]:
        _marcar_primera_atencion(db, tid)
    if hacia in flujo.espera:
        db.execute(text("INSERT INTO ticket_pausa (ticket_id, inicio, motivo) VALUES (:t, now(), :m)"),
                   {"t": tid, "m": nuevo_datos.get("motivo_espera")})
    if t["estado"] in flujo.espera:
        db.execute(text("UPDATE ticket_pausa SET fin=now() WHERE ticket_id=:t AND fin IS NULL"), {"t": tid})
    anterior = {k: t.get(k) for k in set_ if k != "datos"}
    anterior["datos"] = {k: (t["datos"] or {}).get(k) for k in datos}
    nuevo_log = {k: v for k, v in set_.items() if k != "datos"}
    nuevo_log["datos"] = datos
    registrar(db, tid, u.id, "TRANSICION", anterior, nuevo_log, motivo)
    if commit:
        db.commit()
    return obtener(db, tid)


def reabrir(db: Session, u: Usuario, tid: int, motivo: str) -> dict:
    if not (motivo or "").strip():
        raise NegocioError("Debe indicar el motivo de la reapertura.")
    perm.exigir_escritura(db, u)
    t = obtener(db, tid, bloquear=True)
    if t["estado"] != "CERRADO":
        raise NegocioError("Solo se puede reabrir un ticket cerrado.", 409)
    p = perm.permisos_cola(db, u, t["cola_id"])
    if u.id != t["solicitante_id"] and not p.atender:
        raise NegocioError("Solo el solicitante o un agente de la cola puede reabrir.", 403)
    if t["cerrado_en"] and ahora() - t["cerrado_en"] > timedelta(days=DIAS_REAPERTURA):
        raise NegocioError(f"Solo se puede reabrir dentro de {DIAS_REAPERTURA} días desde el cierre.", 409)
    if t["reaperturas"] >= MAX_REAPERTURAS:
        raise NegocioError(f"Se alcanzó el máximo de {MAX_REAPERTURAS} reaperturas; registre un ticket nuevo.", 409)
    flujo = FLUJOS[t["proceso_codigo"]]
    db.execute(text("""UPDATE ticket SET estado=:e, cerrado_en=NULL, resuelto_en=NULL, reaperturas=reaperturas+1,
                       version=version+1, actualizado_en=now() WHERE id=:id"""), {"e": flujo.reapertura, "id": tid})
    registrar(db, tid, u.id, "REAPERTURA", {"estado": "CERRADO", "cerrado_en": t["cerrado_en"]},
              {"estado": flujo.reapertura, "reaperturas": t["reaperturas"] + 1}, motivo)
    db.commit()
    return obtener(db, tid)


# ------------------------------------------------------------------------ comentarios, adjuntos, CI, relaciones
def comentar(db: Session, u: Usuario, tid: int, texto_: str, interno: bool = False) -> dict:
    roles = perm.exigir_escritura(db, u)
    t = obtener(db, tid)
    if not perm.puede_ver_ticket(db, u, t, roles):
        raise NegocioError("No tiene acceso a este ticket.", 403)
    if interno and not perm.permisos_cola(db, u, t["cola_id"], roles).atender:
        raise NegocioError("Solo los agentes de la cola pueden registrar notas internas.", 403)
    if not (texto_ or "").strip():
        raise NegocioError("El comentario está vacío.")
    cid = db.execute(text("INSERT INTO ticket_comentario (ticket_id, autor_id, texto, interno) VALUES (:t,:a,:x,:i) RETURNING id"),
                     {"t": tid, "a": u.id, "x": texto_.strip(), "i": bool(interno)}).scalar()
    registrar(db, tid, u.id, "NOTA_INTERNA" if interno else "COMENTARIO", None, {"comentario_id": cid})
    db.commit()
    return {"id": cid}


def adjuntar(db: Session, u: Usuario, tid: int, nombre: str, tipo: str, contenido: bytes, interno: bool = False) -> dict:
    roles = perm.exigir_escritura(db, u)
    t = obtener(db, tid)
    if not perm.puede_ver_ticket(db, u, t, roles):
        raise NegocioError("No tiene acceso a este ticket.", 403)
    if interno and not perm.permisos_cola(db, u, t["cola_id"], roles).atender:
        raise NegocioError("Solo los agentes pueden subir adjuntos internos.", 403)
    if tipo not in TIPOS_ADJUNTO:
        raise NegocioError(f"Tipo de archivo no permitido ({tipo}). Permitidos: {sorted(TIPOS_ADJUNTO)}.", 415)
    if not contenido or len(contenido) > MAX_ADJUNTO:
        raise NegocioError("El archivo está vacío o supera 2 MB.", 413)
    nombre = (nombre or "adjunto").replace("/", "_").replace("\\", "_")[:200]
    aid = db.execute(text("""INSERT INTO ticket_adjunto (ticket_id, nombre, tipo_mime, tamano, sha256, contenido, interno,
        subido_por) VALUES (:t,:n,:ti,:ta,:s,:c,:i,:u) RETURNING id"""),
        {"t": tid, "n": nombre, "ti": tipo, "ta": len(contenido), "s": hashlib.sha256(contenido).hexdigest(),
         "c": contenido, "i": bool(interno), "u": u.id}).scalar()
    registrar(db, tid, u.id, "ADJUNTO", None, {"adjunto_id": aid, "nombre": nombre, "tamano": len(contenido)})
    db.commit()
    return {"id": aid}


def descargar(db: Session, u: Usuario, tid: int, aid: int) -> dict:
    roles = perm.roles_de(db, u)
    t = obtener(db, tid)
    if not perm.puede_ver_ticket(db, u, t, roles):
        raise NegocioError("No tiene acceso a este ticket.", 403)
    a = db.execute(text("SELECT * FROM ticket_adjunto WHERE id=:a AND ticket_id=:t"), {"a": aid, "t": tid}).mappings().first()
    if a is None:
        raise NegocioError("Adjunto no encontrado.", 404)
    if a["interno"] and not (roles & {"administrador", "consulta"} or perm.permisos_cola(db, u, t["cola_id"], roles).consultar):
        raise NegocioError("Adjunto interno: no autorizado.", 403)
    return dict(a)


def asociar_cis(db: Session, u: Usuario, tid: int, ci_ids: list[int]) -> dict:
    t = obtener(db, tid, bloquear=True)
    if not perm.permisos_cola(db, u, t["cola_id"]).atender:
        raise NegocioError("No tiene permiso para modificar este ticket.", 403)
    ids = sorted({int(x) for x in ci_ids})
    if not ids or db.execute(text("SELECT count(*) FROM ci WHERE id=ANY(:i) AND activo"), {"i": ids}).scalar() != len(ids):
        raise NegocioError("Uno o más CI no existen o están inactivos.", 404)
    for c in ids:
        db.execute(text("INSERT INTO ticket_ci VALUES (:t,:c) ON CONFLICT DO NOTHING"), {"t": tid, "c": c})
    db.execute(text("UPDATE ticket SET sin_ci_motivo=NULL, version=version+1, actualizado_en=now() WHERE id=:id"), {"id": tid})
    registrar(db, tid, u.id, "CI_ASOCIADOS", {"sin_ci_motivo": t["sin_ci_motivo"]}, {"ci_ids": ids})
    db.commit()
    return obtener(db, tid)


REGLAS_RELACION = {"EVENTO_GENERA_INCIDENTE": ("EVENTO", "INCIDENTE"), "INCIDENTE_DE_PROBLEMA": ("INCIDENTE", "PROBLEMA"),
                   "CAMBIO_CORRIGE_PROBLEMA": ("CAMBIO", "PROBLEMA"), "RELACIONADO": (None, None),
                   "DUPLICADO_DE": (None, None)}


def relacionar(db: Session, u: Usuario, origen: int, destino: int, tipo: str, commit: bool = True) -> dict:
    perm.exigir_escritura(db, u)
    if tipo not in REGLAS_RELACION:
        raise NegocioError(f"Tipo de relación inválido. Use {sorted(REGLAS_RELACION)}.")
    o, d = obtener(db, int(origen)), obtener(db, int(destino))
    if not (perm.permisos_cola(db, u, o["cola_id"]).atender or perm.permisos_cola(db, u, d["cola_id"]).atender):
        raise NegocioError("No tiene permiso para relacionar estos tickets.", 403)
    po, pd = REGLAS_RELACION[tipo]
    if po and (o["proceso_codigo"], d["proceso_codigo"]) != (po, pd):
        raise NegocioError(f"La relación {tipo} va de un ticket de {po} a uno de {pd}.", 422)
    if o["id"] == d["id"]:
        raise NegocioError("Un ticket no puede relacionarse consigo mismo.", 422)
    db.execute(text("INSERT INTO ticket_relacion (origen_id, destino_id, tipo, creado_por) VALUES (:o,:d,:t,:u) "
                    "ON CONFLICT DO NOTHING"), {"o": o["id"], "d": d["id"], "t": tipo, "u": u.id})
    registrar(db, o["id"], u.id, "RELACION", None, {"tipo": tipo, "destino": d["numero"]})
    registrar(db, d["id"], u.id, "RELACION", None, {"tipo": tipo, "origen": o["numero"]})
    if commit:
        db.commit()
    return {"origen": o["numero"], "destino": d["numero"], "tipo": tipo}


# ------------------------------------------------------------------------ ingreso de eventos (monitoreo)
def procesar_evento(db: Session, u: Usuario, data: dict) -> dict:
    """Registra un evento por su huella. Procesar de nuevo la misma huella NO duplica ni el evento ni el incidente."""
    huella = (data.get("huella_evento") or "").strip()
    if not huella:
        raise NegocioError("huella_evento es obligatoria para eventos de monitoreo.")
    cat = data.get("categoria_evento")
    if cat not in RESPUESTA_EVENTO:
        raise NegocioError("categoria_evento debe ser E0–E3.")
    previo = db.execute(text("SELECT id FROM ticket WHERE huella_evento=:h"), {"h": huella}).scalar()
    if previo:
        inc = _incidente_de_evento(db, previo)
        return {"evento_id": previo, "incidente_id": inc, "duplicado": True}
    if not perm.permisos_cola(db, u, cola_por_defecto(db, "EVENTO")["id"]).atender:
        raise NegocioError("No tiene permiso para procesar eventos.", 403)
    ev = crear(db, u, {**data, "proceso_codigo": "EVENTO", "canal": "MONITOREO"}, commit=False)
    db.execute(text("UPDATE ticket SET responsable_id=:u WHERE id=:id"), {"u": u.id, "id": ev["id"]})
    transicionar(db, u, ev["id"], "CLASIFICADO", {"categoria_evento": cat}, commit=False)
    inc = None
    if cat in ("E1", "E2", "E3"):
        transicionar(db, u, ev["id"], "EVALUADO", {"evaluacion": data.get("evaluacion") or f"Evaluación automática {cat}"},
                     commit=False)
    if cat in ("E2", "E3"):
        r = transicionar(db, u, ev["id"], "RESPONDIDO", commit=False)
        inc = r["datos"]["incidente_id"]
    db.commit()
    return {"evento_id": ev["id"], "incidente_id": inc, "duplicado": False, "respuesta": RESPUESTA_EVENTO[cat]}
