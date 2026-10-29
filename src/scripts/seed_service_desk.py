"""Datos de demostración del Service Desk: usuarios por rol, colas por proceso y 35+ tickets.

Uso: python -m scripts.seed_service_desk
Requiere: migraciones, import_excel, seed_demo, import_cmdb y generar_cmdb (ver scripts/preparar.sh).
Idempotente: usuarios y colas por código; los tickets de demostración (lote 'DEMO') solo se crean si aún no
existen. Todo avanza por los MISMOS servicios de la aplicación (transiciones, permisos, bitácora).
"""
import os
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.db import SessionLocal
from app.models import Usuario
from app.security import hash_password
from app.services import tickets as tk

COLAS = [  # código, nombre, proceso, nivel, objetivo 1.ª atención (min), objetivo resolución (min)
    ("NS-N1", "Nuevos servicios", "NUEVO_SERVICIO", 1, 480, 14400),
    ("CAM-N2", "Cambios", "CAMBIO", 2, 240, 7200),
    ("INC-N1", "Incidentes — Help Desk", "INCIDENTE", 1, 30, 480),
    ("INC-N2", "Incidentes — Ingenieros", "INCIDENTE", 2, 60, 1440),
    ("INC-N3", "Incidentes — Proveedores", "INCIDENTE", 3, 240, 4320),
    ("EVT-N1", "Eventos de monitoreo", "EVENTO", 1, 15, 240),
    ("PRB-N2", "Problemas", "PROBLEMA", 2, 480, 20160),
    ("ACC-N1", "Accesos", "ACCESO", 1, 120, 1440),
    ("SRV-N1", "Servicios de tecnología", "SERVICIO_TI", 1, 120, 2880),
    ("SRV-N1B", "Servicios de tecnología — campo", "SERVICIO_TI", 1, 240, 2880),
]
USUARIOS = [  # usuario, nombre, rol principal, roles adicionales, puesto
    ("solicitante", "Sofía Solicitante (demo)", "solicitante", [], "TEC-SOP"),
    ("agente1", "Andrés Agente (demo)", "agente", [], "TEC-SOP"),
    ("agente2", "Alicia Agente (demo)", "agente", [], "ING-RED"),
    ("aprobador", "Pedro Aprobador (demo)", "aprobador", ["agente"], "JEFE-APP"),
]


def pw() -> str:
    v = os.getenv("DEMO_USUARIOS_PASSWORD") or os.getenv("DEMO_CONSULTA_PASSWORD")
    if not v or len(v) < 8:
        sys.exit("[sd] defina DEMO_USUARIOS_PASSWORD (o DEMO_CONSULTA_PASSWORD) en .env")
    return v


def usuarios(db) -> dict:
    out = {}
    for login, nombre, rol, extra, puesto in USUARIOS:
        pid = db.execute(text("SELECT id FROM puesto WHERE codigo=:p"), {"p": puesto}).scalar()
        if pid is None:
            sys.exit("[sd] ejecute primero scripts.seed_demo")
        u = db.execute(text("SELECT id FROM usuario WHERE usuario=:u"), {"u": login}).scalar()
        if u is None:
            u = db.execute(text("""INSERT INTO usuario (puesto_id, nombre, usuario, correo, password_hash, rol)
                VALUES (:p, :n, :u, :c, :h, :r) RETURNING id"""), {"p": pid, "n": nombre, "u": login,
                                                                    "c": f"{login}@demo.local", "h": hash_password(pw()), "r": rol}).scalar()
        for r in extra:
            db.execute(text("INSERT INTO usuario_rol VALUES (:u, :r) ON CONFLICT DO NOTHING"), {"u": u, "r": r})
        out[login] = u
    for login in ("admin", "consulta"):
        out[login] = db.execute(text("SELECT id FROM usuario WHERE usuario=:u"), {"u": login}).scalar()
    db.commit()
    return out


def colas(db, us: dict) -> dict:
    out = {}
    for cod, nom, proc, niv, opa, ores in COLAS:
        cid = db.execute(text("SELECT id FROM cola WHERE codigo=:c"), {"c": cod}).scalar()
        if cid is None:
            cid = db.execute(text("""INSERT INTO cola (codigo, nombre, descripcion, proceso_codigo, nivel_soporte,
                objetivo_primera_atencion_min, objetivo_resolucion_min) VALUES (:c,:n,:d,:p,:nv,:o1,:o2) RETURNING id"""),
                             {"c": cod, "n": nom, "d": f"Cola de demostración del proceso {proc}", "p": proc, "nv": niv,
                              "o1": opa, "o2": ores}).scalar()
        out[cod] = cid
        miembros = [dict(rol="agente", puede_consultar=True, puede_atender=True, puede_transferir=True),
                    dict(rol="consulta", puede_consultar=True)]
        if proc in ("NUEVO_SERVICIO", "CAMBIO", "ACCESO"):
            miembros.append(dict(rol="aprobador", puede_consultar=True, puede_aprobar=True))
        if cod == "INC-N1":  # demostración P06: usuario de consulta con 'atender' marcado NO obtiene escritura
            miembros.append(dict(usuario_id=us["consulta"], puede_consultar=True, puede_atender=True))
        for m in miembros:
            db.execute(text("""INSERT INTO cola_miembro (cola_id, usuario_id, rol, puede_consultar, puede_atender,
                puede_transferir, puede_aprobar, puede_administrar) VALUES (:c, :u, :r, :pc, :pa, :pt, :pap, FALSE)
                ON CONFLICT DO NOTHING"""), {"c": cid, "u": m.get("usuario_id"), "r": m.get("rol"),
                                             "pc": m.get("puede_consultar", False), "pa": m.get("puede_atender", False),
                                             "pt": m.get("puede_transferir", False), "pap": m.get("puede_aprobar", False)})
    db.commit()
    return out


def tickets(db, us: dict, cs: dict) -> int:
    if db.execute(text("SELECT count(*) FROM ticket WHERE lote='DEMO'")).scalar():
        print("[sd] los tickets de demostración ya existen; no se duplican")
        return 0
    U = {k: db.get(Usuario, v) for k, v in us.items()}
    srv = dict(db.execute(text("SELECT codigo, id FROM servicio_n2")).all())
    cis = [i for (i,) in db.execute(text("SELECT id FROM ci WHERE lote <> 'MANUAL' ORDER BY id LIMIT 40"))]
    if len(srv) < 46 or len(cis) < 10:
        sys.exit("[sd] faltan servicios importados o CI (ejecute import_excel, import_cmdb y generar_cmdb)")
    base = datetime.now(timezone.utc) - timedelta(days=3)
    n = [0]

    def nuevo(proc, titulo, servicio, quien="solicitante", **kw):
        n[0] += 1
        d = {"titulo": titulo, "descripcion": f"{titulo} (ticket de demostración)", "proceso_codigo": proc,
             "servicio_n2_id": srv[servicio], "impacto": kw.pop("impacto", "MEDIO"), "urgencia": kw.pop("urgencia", "MEDIA"),
             "canal": kw.pop("canal", "PORTAL"), "lote": "DEMO", "creado_en": (base + timedelta(hours=2 * n[0])).isoformat(), **kw}
        return tk.crear(db, U[quien], d)["id"]

    def avanzar(tid, pasos, k):
        if k > 0:
            tk.tomar(db, U["agente1"], tid)
        for hacia, datos, quien in pasos[:k]:
            tk.transicionar(db, U[quien], tid, hacia, datos)

    A, AP, S = "agente1", "aprobador", "solicitante"
    # 1. Nuevos servicios (vincula servicios existentes: no altera el catálogo 12/46)
    p_ns = [("EN_ANALISIS", {"necesidad": "Área solicita el servicio"}, A),
            ("APROBADO", {"viabilidad": "Viable", "servicio_propuesto": {"vincular_id": srv["SE.02.03"]}}, AP),
            ("EN_IMPLEMENTACION", {}, A), ("IMPLEMENTADO", {}, A), ("CERRADO", {}, A)]
    for k in range(5):
        avanzar(nuevo("NUEVO_SERVICIO", f"Nuevo servicio solicitado #{k + 1}", "SE.02.03"), p_ns, k + 1 if k < 4 else 5)
    # 2. Cambios (con CI afectados, plan de pruebas y reversión)
    p_cam = [("EN_EVALUACION", {}, A), ("APROBADO", {"impacto_evaluado": "Medio", "riesgo": "Bajo"}, AP),
             ("PLANIFICADO", {"plan_pruebas": "Pruebas en preproducción", "plan_reversion": "Restaurar respaldo",
                              "fecha_programada": "2026-11-05"}, A),
             ("EN_EJECUCION", {}, A), ("EN_VALIDACION", {"resultado_ejecucion": "Aplicado"}, A),
             ("CERRADO", {"validacion": "Validado por el usuario"}, A)]
    cambios = []
    for k in range(5):
        tid = nuevo("CAMBIO", f"Cambio programado #{k + 1}", "SE.02.01", quien=A, ci_ids=[cis[k]])
        avanzar(tid, p_cam, [0, 1, 2, 4, 6][k])
        cambios.append(tid)
    # 3. Incidentes I0–I6
    inc_def = [("I0", "NINGUNA", None, "BAJO"), ("I1", "INTERRUPCION", 120, "MEDIO"), ("I2", "DEGRADACION", 400, "MEDIO"),
               ("I3", "INTERRUPCION", 2000, "ALTO"), ("I4", "INTERRUPCION", 6000, "ALTO"),
               ("I5", "INTERRUPCION", 50000, "ALTO"), ("I6", "SEGURIDAD", None, "ALTO")]
    incidentes = []
    for k, (cat, af, dur, imp) in enumerate(inc_def):
        p_inc = [("CLASIFICADO", {"categoria_incidente": cat, "afectacion": af, "duracion_estimada_min": dur}, A),
                 ("EN_DIAGNOSTICO", {}, A), ("EN_ATENCION", {"diagnostico": "Diagnóstico inicial"}, A),
                 ("RECUPERADO", {"recuperacion": "Servicio restablecido", "duracion_real_min": dur}, A),
                 ("RESUELTO", {"solucion": "Solución aplicada"}, A), ("CERRADO", {}, S)]
        tid = nuevo("INCIDENTE", f"Incidente {cat} en servicio", "SE.02.02", ci_ids=[cis[10 + k]], impacto=imp,
                    urgencia="ALTA" if imp == "ALTO" else "MEDIA", canal="TELEFONO")
        avanzar(tid, p_inc, [6, 5, 4, 3, 2, 1, 1][k])
        incidentes.append(tid)
    # 4. Eventos E0–E3 por monitoreo (E2/E3 generan incidente) + uno registrado manualmente
    for k, cat in enumerate(["E0", "E1", "E2", "E3"]):
        tk.procesar_evento(db, U[A], {"titulo": f"Evento {cat} de monitoreo", "descripcion": f"Alerta {cat}",
                                      "servicio_n2_id": srv["SE.02.01"], "categoria_evento": cat,
                                      "huella_evento": f"DEMO-MON-{cat}", "ci_ids": [cis[20 + k]], "lote": "DEMO",
                                      "impacto": "ALTO" if cat in ("E2", "E3") else "BAJO", "urgencia": "MEDIA"})
    nuevo("EVENTO", "Evento pendiente de clasificar", "SE.02.01", quien=A, ci_ids=[cis[25]], canal="MONITOREO",
          categoria_evento=None)
    # 5. Problemas (vinculados a incidentes; el último con cambio que implementa la corrección)
    p_prb = [("EN_INVESTIGACION", {}, A), ("CAUSA_IDENTIFICADA", {"causa_raiz": "Configuración incorrecta"}, A),
             ("ERROR_CONOCIDO", {"solucion_temporal": "Reinicio del servicio"}, A),
             ("SOLUCIONADO", {"solucion_definitiva": "Corrección de configuración"}, A), ("CERRADO", {}, A)]
    for k in range(5):
        tid = nuevo("PROBLEMA", f"Problema recurrente #{k + 1}", "SE.02.02", quien=A)
        tk.relacionar(db, U[A], incidentes[k + 1], tid, "INCIDENTE_DE_PROBLEMA")
        if k == 4:
            tk.relacionar(db, U[A], cambios[4], tid, "CAMBIO_CORRIGE_PROBLEMA")
        avanzar(tid, p_prb, [0, 1, 2, 4, 5][k])
    # 6. Accesos (aprobación por el aprobador, nunca por el solicitante)
    p_acc = [("APROBADO", {"recurso": "Sistema contable", "permiso": "Lectura", "accion_acceso": "PROVISION"}, AP),
             ("PROVISIONADO", {}, A), ("VALIDADO", {"validacion": "Acceso verificado"}, A), ("CERRADO", {}, A)]
    for k in range(5):
        tid = nuevo("ACCESO", f"Acceso a sistema contable #{k + 1}", "SE.02.03", beneficiario_id=us["solicitante"],
                    datos={"recurso": "Sistema contable", "permiso": "Lectura", "accion_acceso": "PROVISION"})
        avanzar(tid, p_acc, [0, 1, 2, 3, 4][k])
    # 7. Servicios de tecnología (servicio existente)
    p_srv = [("VALIDADO", {"validacion": "Solicitud válida"}, A), ("ASIGNADO", {}, A), ("EN_CUMPLIMIENTO", {}, A),
             ("CUMPLIDO", {"cumplimiento": "Equipo entregado"}, A), ("CONFIRMADO", {}, S), ("CERRADO", {}, A)]
    for k in range(5):
        avanzar(nuevo("SERVICIO_TI", f"Instalación de punto de red #{k + 1}", "SE.01.01"), p_srv, [0, 1, 3, 4, 6][k])
    db.commit()
    return db.execute(text("SELECT count(*) FROM ticket WHERE lote='DEMO'")).scalar()


def main() -> int:
    db = SessionLocal()
    try:
        us = usuarios(db)
        cs = colas(db, us)
        total = tickets(db, us, cs)
        resumen = db.execute(text("""SELECT proceso_codigo, count(*) FROM ticket WHERE lote='DEMO'
                                     GROUP BY proceso_codigo ORDER BY 1""")).all()
        print(f"[sd] usuarios: {sorted(us)}; colas: {sorted(cs)}; tickets DEMO creados ahora: {total}")
        for p, c in resumen:
            print(f"  {p:15} {c}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
