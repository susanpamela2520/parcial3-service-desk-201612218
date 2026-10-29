"""Indicadores de atención (tablero).

Definiciones (tiempo CALENDARIO, no hábil; ver docs/PROCESOS.md §6):
- antigüedad: para tickets abiertos, ahora − creado_en.
- tiempo de primera atención: primera_atencion_en − creado_en (solo tickets con primera atención).
- tiempo de resolución: resuelto_en − creado_en − pausas (EN_ESPERA) ocurridas antes de resolver.
  Los tickets abiertos sin resolución NO entran al promedio: se cuentan aparte como 'abiertos'.
  Si un ticket se reabrió, se usa su última resolución y se informa el número de reaperturas.
- cumplimiento: % de tickets medibles cuyo tiempo ≤ objetivo configurado en su cola.
"""
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session


def _min(td) -> float:
    return round(td.total_seconds() / 60, 2)


def calcular(db: Session, ahora: datetime | None = None, filtro_lote: str | None = None) -> dict:
    ahora = ahora or datetime.now(timezone.utc)
    p = {"lote": filtro_lote}
    w = "WHERE t.lote = :lote" if filtro_lote else ""
    filas = db.execute(text(f"""
        SELECT t.id, t.proceso_codigo, t.cola_id, c.codigo AS cola, t.prioridad, t.estado, t.creado_en,
               t.primera_atencion_en, t.resuelto_en, t.reaperturas, c.objetivo_primera_atencion_min AS obj_pa,
               c.objetivo_resolucion_min AS obj_res,
               COALESCE((SELECT sum(EXTRACT(EPOCH FROM (COALESCE(pz.fin, :ahora) - pz.inicio)))
                         FROM ticket_pausa pz WHERE pz.ticket_id=t.id
                           AND pz.inicio < COALESCE(t.resuelto_en, :ahora)), 0) AS pausa_seg
        FROM ticket t JOIN cola c ON c.id=t.cola_id {w}"""), {**p, "ahora": ahora}).mappings().all()
    conteo = lambda k: _agrupar(filas, k)  # noqa: E731
    abiertos = [f for f in filas if f["estado"] != "CERRADO"]
    pa = [(f, _min(f["primera_atencion_en"] - f["creado_en"])) for f in filas if f["primera_atencion_en"]]
    res = [(f, max(0.0, _min(f["resuelto_en"] - f["creado_en"]) - float(f["pausa_seg"]) / 60)) for f in filas
           if f["resuelto_en"]]
    return {
        "calculado_en": ahora.isoformat(), "base_tiempo": "calendario",
        "total": len(filas), "abiertos": len(abiertos),
        "por_cola": conteo("cola"), "por_proceso": conteo("proceso_codigo"), "por_prioridad": conteo("prioridad"),
        "por_estado": conteo("estado"),
        "antiguedad_abiertos_min": _stats([_min(ahora - f["creado_en"]) for f in abiertos]),
        "primera_atencion_min": _stats([m for _, m in pa]),
        "resolucion_min": _stats([m for _, m in res]),
        "tickets_sin_resolver_excluidos": len(filas) - len(res),
        "reaperturas": sum(f["reaperturas"] for f in filas),
        "cumplimiento_primera_atencion": _cumple([(m, f["obj_pa"]) for f, m in pa]),
        "cumplimiento_resolucion": _cumple([(m, f["obj_res"]) for f, m in res]),
    }


def _agrupar(filas, k) -> dict:
    d: dict = {}
    for f in filas:
        d[str(f[k])] = d.get(str(f[k]), 0) + 1
    return dict(sorted(d.items()))


def _stats(v: list[float]) -> dict:
    if not v:
        return {"n": 0, "promedio": None, "maximo": None}
    return {"n": len(v), "promedio": round(sum(v) / len(v), 2), "maximo": max(v)}


def _cumple(pares) -> dict:
    medibles = [(m, o) for m, o in pares if o]
    ok = sum(1 for m, o in medibles if m <= o)
    return {"medibles": len(medibles), "cumplen": ok,
            "porcentaje": round(100 * ok / len(medibles), 1) if medibles else None}
