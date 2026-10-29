"""CMDB: dos grafos distintos.

1. Grafo de CLASIFICACIÓN (árbol ci_clase.padre_id): jerarquía Elemento de configuración → Tipo de CI → Clase.
   No admite ciclos (validación en la aplicación + trigger tg_ci_clase_sin_ciclo).
2. Grafo de RELACIONES TÉCNICAS (ci_relacion): depende de, alojado en, componente de, conectado a.
   'conectado a' admite ciclos; los demás no (se valida antes de insertar).

El recorrido de impacto sigue las relaciones que propagan impacto en sentido inverso y devuelve los
servicios POTENCIALMENTE afectados: una dependencia no implica siempre una interrupción efectiva.
"""
import time

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..models import Usuario
from . import permisos as perm
from .errores import NegocioError

ESTADOS_CI = ("Activo", "Inactivo", "En mantenimiento", "Retirado")


def _admin(db: Session, u: Usuario):
    if "administrador" not in perm.roles_de(db, u):
        raise NegocioError("Solo un administrador puede modificar la CMDB.", 403)


# ------------------------------------------------------------------ grafo de clasificación
def clases(db: Session) -> list[dict]:
    return [dict(r) for r in db.execute(text("""
        SELECT c.*, (SELECT count(*) FROM ci WHERE ci.clase_id=c.id) AS cis
        FROM ci_clase c ORDER BY c.nivel, c.codigo""")).mappings()]


def ruta_clase(db: Session, clase_id: int) -> list[dict]:
    return [dict(r) for r in db.execute(text("""
        WITH RECURSIVE r AS (
          SELECT id, codigo, nombre, padre_id, nivel, denominacion, origen, 0 AS prof FROM ci_clase WHERE id=:c
          UNION ALL
          SELECT p.id, p.codigo, p.nombre, p.padre_id, p.nivel, p.denominacion, p.origen, r.prof+1
          FROM ci_clase p JOIN r ON p.id = r.padre_id WHERE r.prof < 50)
        SELECT id, codigo, nombre, nivel, denominacion, origen FROM r ORDER BY nivel"""), {"c": clase_id}).mappings()]


def guardar_clase(db: Session, u: Usuario, data: dict, clase_id: int | None = None) -> dict:
    _admin(db, u)
    codigo, nombre = (data.get("codigo") or "").strip().upper(), (data.get("nombre") or "").strip()
    padre_id = data.get("padre_id")
    if not codigo or not nombre:
        raise NegocioError("Código y nombre de la clase son obligatorios.")
    nivel = 1
    if padre_id:
        padre = db.execute(text("SELECT id, nivel, activo FROM ci_clase WHERE id=:p"), {"p": int(padre_id)}).first()
        if padre is None:
            raise NegocioError("La clase padre indicada no existe.", 404)
        if not padre.activo:
            raise NegocioError("La clase padre está inactiva.", 409)
        if clase_id:
            ciclo = db.execute(text("""WITH RECURSIVE r AS (SELECT id, padre_id FROM ci_clase WHERE id=:p
                UNION ALL SELECT c.id, c.padre_id FROM ci_clase c JOIN r ON c.id=r.padre_id)
                SELECT 1 FROM r WHERE id=:yo"""), {"p": int(padre_id), "yo": clase_id}).first()
            if ciclo or int(padre_id) == clase_id:
                raise NegocioError("La clase padre generaría un ciclo en la jerarquía de clasificación.", 422)
        nivel = padre.nivel + 1
    if db.execute(text("SELECT 1 FROM ci_clase WHERE codigo=:c AND id<>:id"), {"c": codigo, "id": clase_id or 0}).first():
        raise NegocioError(f"Ya existe la clase {codigo}.", 409)
    params = {"c": codigo, "n": nombre, "p": padre_id, "niv": nivel, "den": data.get("denominacion") or "Clase",
              "o": data.get("origen") or "DECISION_PROYECTO", "ip": bool(data.get("requiere_ip")), "id": clase_id}
    if clase_id:
        db.execute(text("""UPDATE ci_clase SET codigo=:c, nombre=:n, padre_id=:p, nivel=:niv, denominacion=:den,
                           origen=:o, requiere_ip=:ip WHERE id=:id"""), params)
    else:
        clase_id = db.execute(text("""INSERT INTO ci_clase (codigo, nombre, padre_id, nivel, denominacion, origen, requiere_ip)
            VALUES (:c,:n,:p,:niv,:den,:o,:ip) RETURNING id"""), params).scalar()
    db.commit()
    return dict(db.execute(text("SELECT * FROM ci_clase WHERE id=:id"), {"id": clase_id}).mappings().first())


# ------------------------------------------------------------------ CI
def buscar(db: Session, f: dict) -> dict:
    t0 = time.perf_counter()
    cond, p = [], {}
    if f.get("q"):
        p["q"] = f"%{f['q'].strip()}%"
        cond.append("(ci.codigo ILIKE :q OR ci.nombre ILIKE :q)")
    if f.get("clase_id"):  # incluye clases descendientes
        p["clase"] = int(f["clase_id"])
        cond.append("""ci.clase_id IN (WITH RECURSIVE d AS (SELECT id FROM ci_clase WHERE id=:clase
                       UNION ALL SELECT c.id FROM ci_clase c JOIN d ON c.padre_id=d.id) SELECT id FROM d)""")
    for k, col in (("estado", "ci.estado_actual"), ("ubicacion_id", "ci.ubicacion_id"), ("lote", "ci.lote"),
                   ("responsable_id", "ci.responsable_id")):
        if f.get(k) not in (None, ""):
            p[k] = f[k]
            cond.append(f"{col}=:{k}")
    if f.get("servicio_n2_id"):
        p["srv"] = int(f["servicio_n2_id"])
        cond.append("ci.id IN (SELECT ci_id FROM ci_servicio WHERE servicio_n2_id=:srv)")
    where = ("WHERE " + " AND ".join(cond)) if cond else ""
    tam = max(1, min(int(f.get("tamano") or 25), 100))
    pag = max(1, int(f.get("pagina") or 1))
    total = db.execute(text(f"SELECT count(*) FROM ci {where}"), p).scalar()
    items = [dict(r) for r in db.execute(text(f"""
        SELECT ci.id, ci.codigo, ci.nombre, ci.estado_actual, cl.codigo AS clase, cl.nombre AS clase_nombre,
               ub.nombre AS ubicacion, us.nombre AS responsable, ci.lote
        FROM ci JOIN ci_clase cl ON cl.id=ci.clase_id JOIN ubicacion ub ON ub.id=ci.ubicacion_id
        JOIN usuario us ON us.id=ci.responsable_id {where}
        ORDER BY ci.codigo LIMIT :lim OFFSET :off"""), {**p, "lim": tam, "off": (pag - 1) * tam}).mappings()]
    return {"total": total, "pagina": pag, "tamano": tam, "paginas": max(1, -(-total // tam)), "items": items,
            "tiempo_ms": round((time.perf_counter() - t0) * 1000, 2)}


def detalle(db: Session, ci_id: int) -> dict:
    c = db.execute(text("""SELECT ci.*, ub.nombre AS ubicacion, us.nombre AS responsable FROM ci
        JOIN ubicacion ub ON ub.id=ci.ubicacion_id JOIN usuario us ON us.id=ci.responsable_id WHERE ci.id=:id"""),
                   {"id": ci_id}).mappings().first()
    if c is None:
        raise NegocioError(f"No existe el CI con id {ci_id}.", 404)
    d = dict(c)
    d["clasificacion"] = ruta_clase(db, c["clase_id"])
    rel = lambda sql: [dict(r) for r in db.execute(text(sql), {"id": ci_id}).mappings()]  # noqa: E731
    d["relaciones_salientes"] = rel("""SELECT r.id, r.tipo, t.nombre AS tipo_nombre, x.id AS ci_id, x.codigo, x.nombre
        FROM ci_relacion r JOIN ci x ON x.id=r.destino_id JOIN ci_relacion_tipo t ON t.codigo=r.tipo
        WHERE r.origen_id=:id ORDER BY r.tipo, x.codigo""")
    d["relaciones_entrantes"] = rel("""SELECT r.id, r.tipo, t.nombre AS tipo_nombre, x.id AS ci_id, x.codigo, x.nombre
        FROM ci_relacion r JOIN ci x ON x.id=r.origen_id JOIN ci_relacion_tipo t ON t.codigo=r.tipo
        WHERE r.destino_id=:id ORDER BY r.tipo, x.codigo""")
    d["servicios"] = rel("""SELECT s.id, s.codigo, s.nombre FROM ci_servicio cs JOIN servicio_n2 s ON s.id=cs.servicio_n2_id
        WHERE cs.ci_id=:id ORDER BY s.codigo""")
    d["tickets"] = rel("""SELECT t.id, t.numero, t.titulo, t.proceso_codigo, t.estado, t.creado_en FROM ticket_ci tc
        JOIN ticket t ON t.id=tc.ticket_id WHERE tc.ci_id=:id ORDER BY t.creado_en DESC""")
    return d


def _existe_camino(db: Session, desde: int, hasta: int, tipo: str) -> bool:
    return db.execute(text("""WITH RECURSIVE r(id, prof) AS (SELECT CAST(:d AS int), 0
        UNION SELECT x.destino_id, r.prof+1 FROM ci_relacion x JOIN r ON x.origen_id=r.id
        WHERE x.tipo=:t AND r.prof < 1000) SELECT 1 FROM r WHERE id=:h LIMIT 1"""),
                      {"d": desde, "h": hasta, "t": tipo}).first() is not None


def relacionar(db: Session, u: Usuario, origen: int, destino: int, tipo: str) -> dict:
    _admin(db, u)
    t = db.execute(text("SELECT * FROM ci_relacion_tipo WHERE codigo=:t"), {"t": tipo}).mappings().first()
    if t is None:
        raise NegocioError("Tipo de relación inválido.", 422)
    origen, destino = int(origen), int(destino)
    if origen == destino:
        raise NegocioError("Un CI no puede relacionarse consigo mismo.", 422)
    if db.execute(text("SELECT count(*) FROM ci WHERE id IN (:a,:b)"), {"a": origen, "b": destino}).scalar() != 2:
        raise NegocioError("Uno de los CI no existe.", 404)
    if not t["admite_ciclos"] and _existe_camino(db, destino, origen, tipo):
        raise NegocioError(f"La relación '{t['nombre']}' no admite ciclos y esta relación crearía uno.", 422)
    rid = db.execute(text("""INSERT INTO ci_relacion (origen_id, destino_id, tipo) VALUES (:o,:d,:t)
        ON CONFLICT DO NOTHING RETURNING id"""), {"o": origen, "d": destino, "t": tipo}).scalar()
    if rid is None:
        raise NegocioError("La relación ya existe.", 409)
    db.commit()
    return {"id": rid}


def impacto(db: Session, ci_id: int, max_prof: int = 10) -> dict:
    """CIs y servicios POTENCIALMENTE afectados si falla ci_id.

    Propagación: si X falla, se afecta quien DEPENDE_DE X, quien está ALOJADO_EN X, y aquello de lo
    que X es COMPONENTE_DE. 'conectado a' no propaga. El uso de UNION + profundidad evita ciclos.
    """
    if not db.execute(text("SELECT 1 FROM ci WHERE id=:i"), {"i": ci_id}).first():
        raise NegocioError(f"No existe el CI con id {ci_id}.", 404)
    filas = db.execute(text("""
        WITH RECURSIVE afectado(id, prof, via) AS (
          SELECT CAST(:i AS int), 0, CAST('' AS text)
          UNION
          SELECT n.sig, a.prof + 1, n.tipo FROM afectado a JOIN LATERAL (
              SELECT r.origen_id AS sig, r.tipo FROM ci_relacion r
                WHERE r.destino_id = a.id AND r.tipo IN ('DEPENDE_DE', 'ALOJADO_EN')
              UNION ALL
              SELECT r.destino_id, r.tipo FROM ci_relacion r WHERE r.origen_id = a.id AND r.tipo = 'COMPONENTE_DE'
          ) n ON TRUE WHERE a.prof < :mp)
        SELECT a.id, min(a.prof) AS prof, ci.codigo, ci.nombre FROM afectado a JOIN ci ON ci.id=a.id
        GROUP BY a.id, ci.codigo, ci.nombre ORDER BY prof, ci.codigo"""), {"i": ci_id, "mp": max_prof}).mappings().all()
    ids = [f["id"] for f in filas]
    servicios = [dict(r) for r in db.execute(text("""SELECT DISTINCT s.id, s.codigo, s.nombre FROM ci_servicio cs
        JOIN servicio_n2 s ON s.id=cs.servicio_n2_id WHERE cs.ci_id = ANY(:ids) ORDER BY s.codigo"""), {"ids": ids}).mappings()]
    return {"ci_id": ci_id, "cis_afectados": [dict(f) for f in filas], "servicios_potencialmente_afectados": servicios,
            "nota": "Una dependencia indica impacto potencial, no una interrupción efectiva confirmada."}


def resumen(db: Session, lote: str | None = None) -> dict:
    p = {"lote": lote}
    filtro = "WHERE ci.lote = :lote" if lote else ""
    por_clase = [dict(r) for r in db.execute(text(f"""
        SELECT cl.nivel, cl.codigo, cl.nombre, p.nombre AS tipo, count(ci.id) AS dispositivos
        FROM ci JOIN ci_clase cl ON cl.id=ci.clase_id LEFT JOIN ci_clase p ON p.id=cl.padre_id {filtro}
        GROUP BY cl.nivel, cl.codigo, cl.nombre, p.nombre ORDER BY cl.codigo"""), p).mappings()]
    por_tipo = [dict(r) for r in db.execute(text(f"""
        SELECT p.codigo, p.nombre, count(ci.id) AS dispositivos FROM ci JOIN ci_clase cl ON cl.id=ci.clase_id
        JOIN ci_clase p ON p.id=cl.padre_id {filtro} GROUP BY p.codigo, p.nombre ORDER BY p.codigo"""), p).mappings()]
    total = db.execute(text(f"SELECT count(*) FROM ci {filtro}"), p).scalar()
    distintos = db.execute(text(f"SELECT count(DISTINCT codigo) FROM ci {filtro}"), p).scalar()
    relaciones = db.execute(text("SELECT tipo, count(*) FROM ci_relacion GROUP BY tipo ORDER BY tipo")).all()
    return {"lote": lote, "total_dispositivos": total, "codigos_distintos": distintos, "por_tipo_de_ci": por_tipo,
            "por_clase": por_clase, "relaciones": {t: n for t, n in relaciones}}
