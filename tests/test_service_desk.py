"""Parcial 3 — escenarios P01–P17 del Service Desk y la CMDB (integración API + PostgreSQL catalogo_test).

P18 (reinicio de contenedores) es extremo a extremo: scripts/p18_persistencia.sh.
"""
import io
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, InternalError, ProgrammingError

from app.importador import importar
from app.main import app
from app.models import Usuario
from app.security import hash_password
from app.services import indicadores
from app.services import tickets as tk
from conftest import PW_ADMIN, PW_CONSULTA, login
from scripts.generar_cmdb import generar

PW = "Demo-Prueba-123"
COLAS = [("NS", "NUEVO_SERVICIO", 1), ("CAM", "CAMBIO", 2), ("INC1", "INCIDENTE", 1), ("INC1B", "INCIDENTE", 1),
         ("INC2", "INCIDENTE", 2), ("EVT", "EVENTO", 1), ("PRB", "PROBLEMA", 2), ("ACC", "ACCESO", 1),
         ("SRV", "SERVICIO_TI", 1)]


@pytest.fixture
def sd(db, org):
    """Entorno controlado: usuarios por rol, colas por proceso, servicios y CI de prueba."""
    ids = {"admin": org["admin"].id, "consulta": org["consulta"].id}
    for login_, rol, extra in (("sol", "solicitante", []), ("sol2", "solicitante", []), ("ag1", "agente", []),
                               ("ag2", "agente", []), ("apr", "aprobador", ["agente"])):
        u = Usuario(nombre=login_, usuario=login_, rol=rol, puesto_id=org["p1"].id, password_hash=hash_password(PW))
        db.add(u)
        db.flush()
        for r in extra:
            db.execute(text("INSERT INTO usuario_rol VALUES (:u, :r)"), {"u": u.id, "r": r})
        ids[login_] = u.id
    n1 = db.execute(text("INSERT INTO servicio_n1 (codigo, nombre) VALUES ('SE.01', 'Infraestructura') RETURNING id")).scalar()
    srv = {}
    for c in ("SE.01.01", "SE.01.02"):
        srv[c] = db.execute(text("INSERT INTO servicio_n2 (servicio_n1_id, codigo, nombre, activo) VALUES (:n, :c, :c, 'S') "
                                 "RETURNING id"), {"n": n1, "c": c}).scalar()
    colas = {}
    for cod, proc, niv in COLAS:
        colas[cod] = db.execute(text("""INSERT INTO cola (codigo, nombre, proceso_codigo, nivel_soporte,
            objetivo_primera_atencion_min, objetivo_resolucion_min) VALUES (:c, :c, :p, :n, 30, 480) RETURNING id"""),
                                {"c": cod, "p": proc, "n": niv}).scalar()
        db.execute(text("""INSERT INTO cola_miembro (cola_id, rol, puede_consultar, puede_atender, puede_transferir)
            VALUES (:c, 'agente', TRUE, TRUE, TRUE)"""), {"c": colas[cod]})
        if proc in ("NUEVO_SERVICIO", "CAMBIO", "ACCESO"):
            db.execute(text("INSERT INTO cola_miembro (cola_id, rol, puede_consultar, puede_aprobar) VALUES (:c, 'aprobador', TRUE, TRUE)"),
                       {"c": colas[cod]})
    # consulta es miembro de INC1 con 'atender' marcado: no debe obtener escritura (P06)
    db.execute(text("INSERT INTO cola_miembro (cola_id, usuario_id, puede_consultar, puede_atender) VALUES (:c, :u, TRUE, TRUE)"),
               {"c": colas["INC1"], "u": ids["consulta"]})
    clase = dict(db.execute(text("SELECT codigo, id FROM ci_clase")).all())
    ub = db.execute(text("SELECT id FROM ubicacion ORDER BY id LIMIT 1")).scalar()
    cis = {}
    for cod, cl in (("SRV1", "HW-SRV"), ("APP1", "SW-APP"), ("BD1", "SW-BD"), ("NET1", "HW-RED")):
        cis[cod] = db.execute(text("""INSERT INTO ci (codigo, nombre, clase_id, estado_actual, ubicacion_id, responsable_id)
            VALUES (:c, :c, :cl, 'Activo', :u, :r) RETURNING id"""), {"c": cod, "cl": clase[cl], "u": ub, "r": ids["admin"]}).scalar()
    db.commit()
    return {"u": ids, "srv": srv, "colas": colas, "cis": cis}


def cliente(usuario, pw=PW):
    c = TestClient(app)
    assert login(c, usuario, pw).status_code == 200, usuario
    return c


def U(db, sd, nombre):
    return db.get(Usuario, sd["u"][nombre])


def nuevo(c, sd, proceso, **kw):
    datos = {"titulo": f"Ticket {proceso}", "descripcion": "Prueba", "proceso_codigo": proceso,
             "servicio_n2_id": sd["srv"]["SE.01.01"], "impacto": "MEDIO", "urgencia": "MEDIA", **kw}
    if proceso in ("INCIDENTE", "CAMBIO", "EVENTO") and "ci_ids" not in kw and "sin_ci_motivo" not in kw:
        datos["ci_ids"] = [sd["cis"]["SRV1"]]
    r = c.post("/api/tickets", json=datos)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def tr(c, tid, hacia, **datos):
    return c.post(f"/api/tickets/{tid}/transicion", json={"hacia": hacia, "datos": datos})


# ---------------------------------------------------------------------------------------- P01
def test_p01_autenticacion_roles_y_rechazos(sd, db):
    ag = cliente("ag1")
    assert ag.get("/api/mis-roles").json()["roles"] == ["agente"]
    assert cliente("apr").get("/api/mis-roles").json()["roles"] == ["agente", "aprobador"]
    assert login(TestClient(app), "ag1", "mala").status_code == 401
    sol = cliente("sol")
    assert sol.post("/api/colas", json={"codigo": "X", "nombre": "X", "proceso_codigo": "INCIDENTE",
                                        "nivel_soporte": 1}).status_code == 403
    con = cliente("consulta", PW_CONSULTA)
    assert con.post("/api/tickets", json={"titulo": "x"}).status_code == 403
    assert con.get("/api/tickets").status_code == 200
    sol.post("/api/auth/logout")
    assert sol.get("/api/tickets").status_code == 401
    adm = cliente("admin", PW_ADMIN)
    assert adm.post(f"/api/usuarios/{sd['u']['ag1']}/desactivar").status_code == 200
    assert ag.get("/api/tickets").status_code == 401


# ---------------------------------------------------------------------------------------- P02
def test_p02_importacion_repetible_controles(db):
    from app import config
    r1 = importar(db, config.EXCEL_PATH)
    r2 = importar(db, config.EXCEL_PATH)
    assert (r1["control"]["n1_encontrados"], r1["control"]["n2_encontrados"]) == (12, 46)
    assert r2["n2"]["creados"] == 0 and r2["n2"]["sin_cambios"] == 46
    assert r2["observaciones_por_tipo"]["CONFLICTO_NOMBRE_N1"] == 1


# ---------------------------------------------------------------------------------------- P03
def test_p03_generador_5000_unicos_y_repetible(db, org):
    from app import config
    importar(db, config.EXCEL_PATH)
    r1 = generar(db, 77, 5000)
    r2 = generar(db, 77, 5000)
    assert r1["insertados"] == 5000 and r2["insertados"] == 0
    total, distintos = db.execute(text("SELECT count(*), count(DISTINCT codigo) FROM ci WHERE lote='SEMILLA-77'")).one()
    assert total == distintos == 5000
    por_tipo = db.execute(text("""SELECT p.codigo, count(*) FROM ci JOIN ci_clase c ON c.id=ci.clase_id
        JOIN ci_clase p ON p.id=c.padre_id WHERE ci.lote='SEMILLA-77' GROUP BY p.codigo""")).all()
    assert {c for c, _ in por_tipo} == {"HW", "SW", "MED"} and sum(n for _, n in por_tipo) == 5000
    sin_ip = db.execute(text("""SELECT count(*) FROM ci JOIN ci_clase c ON c.id=ci.clase_id
        WHERE ci.lote='SEMILLA-77' AND NOT c.requiere_ip AND ci.atributos ? 'ip'""")).scalar()
    assert sin_ip == 0  # no se asigna IP a clases que no la requieren
    huerfanos = db.execute(text("SELECT count(*) FROM ci_relacion r LEFT JOIN ci o ON o.id=r.origen_id WHERE o.id IS NULL")).scalar()
    assert huerfanos == 0


# ---------------------------------------------------------------------------------------- P04
def test_p04_jerarquia_fuentes_padres_y_ciclos(sd, db):
    adm = cliente("admin", PW_ADMIN)
    cl = {c["codigo"]: c for c in adm.get("/api/cmdb/clases").json()}
    assert cl["CI"]["nivel"] == 1 and "diap. 12" in cl["CI"]["origen"]
    assert cl["HW"]["origen"].startswith("CMDB.xlsx") and "C5" in cl["HW"]["origen"]
    assert cl["SW"]["origen"].startswith("CMDB.xlsx") and "C6" in cl["SW"]["origen"]
    assert all(c["padre_id"] == cl["CI"]["id"] for k, c in cl.items() if c["nivel"] == 2)
    assert adm.post("/api/cmdb/clases", json={"codigo": "T-X", "nombre": "X", "padre_id": 99999}).status_code == 404
    a = adm.post("/api/cmdb/clases", json={"codigo": "T-A", "nombre": "A", "padre_id": cl["HW"]["id"]}).json()
    b = adm.post("/api/cmdb/clases", json={"codigo": "T-B", "nombre": "B", "padre_id": a["id"]}).json()
    r = adm.put(f"/api/cmdb/clases/{a['id']}", json={"codigo": "T-A", "nombre": "A", "padre_id": b["id"]})
    assert r.status_code == 422 and "ciclo" in r.json()["detail"]
    with pytest.raises((IntegrityError, InternalError, ProgrammingError)):
        db.execute(text("UPDATE ci_clase SET padre_id=:b WHERE id=:a"), {"a": a["id"], "b": b["id"]})
    db.rollback()
    assert cliente("ag1").post("/api/cmdb/clases", json={"codigo": "T-Z", "nombre": "Z"}).status_code == 403


# ---------------------------------------------------------------------------------------- P05
def test_p05_creacion_con_referencias_validas_e_invalidas(sd):
    sol = cliente("sol")
    tid = nuevo(sol, sd, "SERVICIO_TI")
    t = sol.get(f"/api/tickets/{tid}").json()
    assert t["numero"].startswith("TK-") and t["servicio"]["n1_codigo"] == "SE.01" and t["estado"] == "SOLICITADO"
    assert t["solicitante_id"] == t["creador_id"] == sd["u"]["sol"] and t["sin_ci_motivo"]
    base = {"titulo": "x", "descripcion": "y", "proceso_codigo": "INCIDENTE", "servicio_n2_id": sd["srv"]["SE.01.01"]}
    assert sol.post("/api/tickets", json={**base, "servicio_n2_id": 9999, "ci_ids": [sd["cis"]["SRV1"]]}).status_code == 404
    assert sol.post("/api/tickets", json={**base, "proceso_codigo": "XYZ"}).status_code == 422
    assert sol.post("/api/tickets", json={**base, "ci_ids": [9999]}).status_code == 404
    assert sol.post("/api/tickets", json=base).status_code == 422  # sin CI y sin motivo
    assert sol.post("/api/tickets", json={**base, "sin_ci_motivo": "Consulta general"}).status_code == 201
    r = sol.post("/api/tickets", json={**base, "ci_ids": [sd["cis"]["SRV1"]], "cola_id": sd["colas"]["CAM"]})
    assert r.status_code == 422 and "proceso" in r.json()["detail"]


# ---------------------------------------------------------------------------------------- P06
def test_p06_cola_por_rol_y_usuario_permisos(sd, db):
    sol, ag, con = cliente("sol"), cliente("ag1"), cliente("consulta", PW_CONSULTA)
    tid = nuevo(sol, sd, "INCIDENTE")
    assert con.get(f"/api/tickets/{tid}").status_code == 200           # lectura permitida
    assert con.post(f"/api/tickets/{tid}/tomar").status_code == 403    # 'atender' marcado no eleva a consulta
    p = con.get(f"/api/colas/{sd['colas']['INC1']}/mis-permisos").json()
    assert p["consultar"] and not p["atender"]
    assert cliente("sol2").get(f"/api/tickets/{tid}").status_code == 403  # otro solicitante
    assert ag.post(f"/api/tickets/{tid}/tomar").status_code == 200        # agente por rol
    # cola asignada a un usuario concreto (sin rol agente)
    adm = cliente("admin", PW_ADMIN)
    cid = sd["colas"]["SRV"]
    assert adm.post(f"/api/colas/{cid}/miembros", json={"usuario_id": sd["u"]["sol2"], "puede_atender": True}).status_code == 201
    assert cliente("sol2").get(f"/api/colas/{cid}/mis-permisos").json()["atender"] is False  # solicitante: sin escritura
    assert adm.post(f"/api/colas/{cid}/miembros", json={"usuario_id": sd["u"]["ag2"], "puede_atender": True}).status_code == 201
    assert cliente("ag2").get(f"/api/colas/{cid}/mis-permisos").json()["atender"] is True


# ---------------------------------------------------------------------------------------- P07
def test_p07_ordenamiento_y_toma_concurrente(sd, db):
    sol = cliente("sol")
    baja = nuevo(sol, sd, "SERVICIO_TI", impacto="BAJO", urgencia="BAJA")
    alta1 = nuevo(sol, sd, "SERVICIO_TI", impacto="ALTO", urgencia="ALTA")
    alta2 = nuevo(sol, sd, "SERVICIO_TI", impacto="ALTO", urgencia="ALTA")
    orden = [t["id"] for t in cliente("ag1").get(f"/api/colas/{sd['colas']['SRV']}/siguientes").json()]
    assert orden == [alta1, alta2, baja]  # prioridad y luego antigüedad (y id)
    resultados = []
    barrera = threading.Barrier(2)

    def tomar(usuario):
        c = cliente(usuario)
        barrera.wait()
        resultados.append((usuario, c.post(f"/api/tickets/{baja}/tomar").status_code))

    hilos = [threading.Thread(target=tomar, args=(u,)) for u in ("ag1", "ag2")]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    assert sorted(c for _, c in resultados) == [200, 409]
    ganador = [u for u, c in resultados if c == 200][0]
    assert db.execute(text("SELECT responsable_id FROM ticket WHERE id=:t"), {"t": baja}).scalar() == sd["u"][ganador]
    assert cliente("ag2").post(f"/api/colas/{sd['colas']['SRV']}/tomar-siguiente").json()["id"] == alta1


# ---------------------------------------------------------------------------------------- P08
def test_p08_transferencia_con_historial_y_validacion(sd, db):
    tid = nuevo(cliente("sol"), sd, "INCIDENTE")
    ag = cliente("ag1")
    ag.post(f"/api/tickets/{tid}/tomar")
    assert ag.post(f"/api/tickets/{tid}/transferir", json={"cola_id": sd["colas"]["INC1B"]}).status_code == 422  # sin motivo
    r = ag.post(f"/api/tickets/{tid}/transferir", json={"cola_id": sd["colas"]["CAM"], "motivo": "x"})
    assert r.status_code == 422 and "proceso" in r.json()["detail"]
    r = ag.post(f"/api/tickets/{tid}/transferir", json={"cola_id": sd["colas"]["INC1B"], "motivo": "Otro equipo"})
    assert r.status_code == 200 and r.json()["cola_id"] == sd["colas"]["INC1B"] and r.json()["responsable_id"] is None
    log = [b for b in ag.get(f"/api/tickets/{tid}").json()["bitacora"] if b["accion"] == "TRANSFERENCIA"]
    assert log[0]["anterior"]["cola_id"] == sd["colas"]["INC1"] and log[0]["motivo"] == "Otro equipo"
    r = ag.post(f"/api/tickets/{tid}/escalar", json={"cola_id": sd["colas"]["INC2"], "motivo": "Requiere ingeniero"})
    assert r.status_code == 200 and r.json()["nivel_soporte"] == 2


# ---------------------------------------------------------------------------------------- P09
RUTAS = {
    "NUEVO_SERVICIO": [("EN_ANALISIS", "ag1", {"necesidad": "n"}),
                       ("APROBADO", "apr", {"viabilidad": "ok", "servicio_propuesto": {"codigo": "SE.01.09", "nombre": "Nuevo",
                                                                                        "servicio_n1_id": None}}),
                       ("EN_IMPLEMENTACION", "ag1", {}), ("IMPLEMENTADO", "ag1", {}), ("CERRADO", "ag1", {})],
    "CAMBIO": [("EN_EVALUACION", "ag1", {}), ("APROBADO", "apr", {"impacto_evaluado": "m", "riesgo": "b"}),
               ("PLANIFICADO", "ag1", {"plan_pruebas": "p", "plan_reversion": "r", "fecha_programada": "2026-11-01"}),
               ("EN_EJECUCION", "ag1", {}), ("EN_VALIDACION", "ag1", {"resultado_ejecucion": "ok"}),
               ("CERRADO", "ag1", {"validacion": "ok"})],
    "INCIDENTE": [("CLASIFICADO", "ag1", {"categoria_incidente": "I1", "afectacion": "INTERRUPCION", "duracion_estimada_min": 90}),
                  ("EN_DIAGNOSTICO", "ag1", {}), ("EN_ATENCION", "ag1", {"diagnostico": "d"}),
                  ("RECUPERADO", "ag1", {"recuperacion": "r"}), ("RESUELTO", "ag1", {"solucion": "s"}), ("CERRADO", "sol", {})],
    "EVENTO": [("CLASIFICADO", "ag1", {"categoria_evento": "E1"}), ("EVALUADO", "ag1", {"evaluacion": "sin impacto"}),
               ("CERRADO", "ag1", {})],
    "PROBLEMA": [("EN_INVESTIGACION", "ag1", {}), ("CAUSA_IDENTIFICADA", "ag1", {"causa_raiz": "c"}),
                 ("ERROR_CONOCIDO", "ag1", {"solucion_temporal": "t"}), ("SOLUCIONADO", "ag1", {"solucion_definitiva": "d"}),
                 ("CERRADO", "ag1", {})],
    "ACCESO": [("APROBADO", "apr", {"recurso": "ERP", "permiso": "Lectura", "accion_acceso": "PROVISION"}),
               ("PROVISIONADO", "ag1", {}), ("VALIDADO", "ag1", {"validacion": "ok"}), ("CERRADO", "ag1", {})],
    "SERVICIO_TI": [("VALIDADO", "ag1", {"validacion": "ok"}), ("ASIGNADO", "ag1", {}), ("EN_CUMPLIMIENTO", "ag1", {}),
                    ("CUMPLIDO", "ag1", {"cumplimiento": "ok"}), ("CONFIRMADO", "sol", {}), ("CERRADO", "ag1", {})],
}
INVALIDAS = {"NUEVO_SERVICIO": "IMPLEMENTADO", "CAMBIO": "EN_EJECUCION", "INCIDENTE": "CERRADO", "EVENTO": "RESPONDIDO",
             "PROBLEMA": "SOLUCIONADO", "ACCESO": "PROVISIONADO", "SERVICIO_TI": "CUMPLIDO"}


@pytest.mark.parametrize("proceso", list(RUTAS))
def test_p09_ruta_completa_y_transiciones_invalidas(sd, db, proceso):
    c = {k: cliente(k) for k in ("sol", "ag1", "apr")}
    extra = {"beneficiario_id": sd["u"]["sol2"]} if proceso == "ACCESO" else {}
    tid = nuevo(c["sol"], sd, proceso, **extra)
    if proceso == "PROBLEMA":
        inc = nuevo(c["sol"], sd, "INCIDENTE")
        assert c["ag1"].post(f"/api/tickets/{inc}/relaciones", json={"destino_id": tid, "tipo": "INCIDENTE_DE_PROBLEMA"}).status_code == 201
    r = tr(c["ag1"], tid, INVALIDAS[proceso])
    assert r.status_code == 409, r.text
    c["ag1"].post(f"/api/tickets/{tid}/tomar")
    for hacia, quien, datos in RUTAS[proceso]:
        if "servicio_propuesto" in datos:
            datos["servicio_propuesto"]["servicio_n1_id"] = db.execute(text("SELECT id FROM servicio_n1")).scalar()
        r = tr(c[quien], tid, hacia, **datos)
        assert r.status_code == 200, f"{proceso} → {hacia}: {r.text}"
    t = c["ag1"].get(f"/api/tickets/{tid}").json()
    assert t["estado"] == "CERRADO" and t["cerrado_en"] and t["resuelto_en"]
    assert [b["nuevo"]["estado"] for b in t["bitacora"] if b["accion"] == "TRANSICION"] == [h for h, _, _ in RUTAS[proceso]]
    if proceso == "NUEVO_SERVICIO":  # la aprobación crea el servicio sin duplicarlo
        assert db.execute(text("SELECT count(*) FROM servicio_n2 WHERE codigo='SE.01.09'")).scalar() == 1
        t2 = nuevo(c["sol"], sd, proceso)
        c["ag1"].post(f"/api/tickets/{t2}/tomar")
        for hacia, quien, datos in RUTAS[proceso][:4]:
            assert tr(c[quien], t2, hacia, **datos).status_code == 200
        assert db.execute(text("SELECT count(*) FROM servicio_n2 WHERE codigo='SE.01.09'")).scalar() == 1


# ---------------------------------------------------------------------------------------- P10
def test_p10_cambio_sin_aprobacion_y_autoaprobacion(sd, db):
    apr, ag = cliente("apr"), cliente("ag1")
    cam = nuevo(ag, sd, "CAMBIO")
    ag.post(f"/api/tickets/{cam}/tomar")
    assert tr(ag, cam, "EN_EVALUACION").status_code == 200
    assert tr(ag, cam, "EN_EJECUCION").status_code == 409                                   # sin aprobación
    assert tr(ag, cam, "APROBADO", impacto_evaluado="a", riesgo="b").status_code == 403      # agente sin rol aprobador
    # el aprobador como solicitante de su propio acceso no puede aprobarlo
    acc = nuevo(apr, sd, "ACCESO", beneficiario_id=sd["u"]["sol"])
    r = tr(apr, acc, "APROBADO", recurso="ERP", permiso="Admin", accion_acceso="PROVISION")
    assert r.status_code == 403 and "propia" in r.json()["detail"]
    acc2 = nuevo(cliente("sol"), sd, "ACCESO", beneficiario_id=sd["u"]["apr"])
    r = tr(apr, acc2, "APROBADO", recurso="ERP", permiso="Admin", accion_acceso="PROVISION")
    assert r.status_code == 403 and "beneficiario" in r.json()["detail"]
    with pytest.raises(IntegrityError):  # defensa en la BD
        db.execute(text("UPDATE ticket SET aprobado_por_id=solicitante_id WHERE id=:t"), {"t": acc})
    db.rollback()


# ---------------------------------------------------------------------------------------- P11
def test_p11_eventos_e0_e3_y_sin_duplicar(sd, db):
    ag = cliente("ag1")
    base = {"titulo": "Alerta", "descripcion": "monitoreo", "servicio_n2_id": sd["srv"]["SE.01.01"],
            "ci_ids": [sd["cis"]["SRV1"]]}
    res = {}
    for cat in ("E0", "E1", "E2", "E3"):
        r = ag.post("/api/eventos", json={**base, "categoria_evento": cat, "huella_evento": f"H-{cat}"})
        assert r.status_code == 201, r.text
        res[cat] = r.json()
    assert res["E0"]["incidente_id"] is None and res["E1"]["incidente_id"] is None
    assert res["E2"]["incidente_id"] and res["E3"]["incidente_id"]
    estados = {cat: ag.get(f"/api/tickets/{v['evento_id']}").json()["estado"] for cat, v in res.items()}
    assert estados == {"E0": "CLASIFICADO", "E1": "EVALUADO", "E2": "RESPONDIDO", "E3": "RESPONDIDO"}
    inc3 = ag.get(f"/api/tickets/{res['E3']['incidente_id']}").json()
    assert inc3["proceso_codigo"] == "INCIDENTE" and inc3["afectacion"] == "SEGURIDAD"
    notif = db.execute(text("SELECT count(*) FROM notificacion WHERE ticket_id IN (:a, :b)"),
                       {"a": res["E2"]["incidente_id"], "b": res["E3"]["incidente_id"]}).scalar()
    assert notif >= 2
    antes = db.execute(text("SELECT count(*) FROM ticket")).scalar()
    otra = ag.post("/api/eventos", json={**base, "categoria_evento": "E2", "huella_evento": "H-E2"}).json()
    assert otra["duplicado"] and otra["incidente_id"] == res["E2"]["incidente_id"]
    assert db.execute(text("SELECT count(*) FROM ticket")).scalar() == antes
    assert tr(ag, res["E0"]["evento_id"], "CERRADO").status_code == 200   # E0: registro únicamente
    assert tr(ag, res["E1"]["evento_id"], "CERRADO").status_code == 200   # E1: evaluación conservada
    e = nuevo(ag, sd, "EVENTO")
    tr(ag, e, "CLASIFICADO", categoria_evento="E2")
    assert tr(ag, e, "CERRADO").status_code == 409                       # E2 no se cierra sin respuesta


# ---------------------------------------------------------------------------------------- P12
def test_p12_incidentes_i0_i6_reglas_complementarias(sd, db):
    ag, sol = cliente("ag1"), cliente("sol")
    casos = [("I0", "NINGUNA", None, True, 4), ("I1", "INTERRUPCION", 180, False, 3), ("I2", "DEGRADACION", 181, False, 3),
             ("I3", "INTERRUPCION", 2880, False, 2), ("I4", "INTERRUPCION", 10080, False, 2),
             ("I5", "INTERRUPCION", 20000, False, 2), ("I6", "SEGURIDAD", None, False, 2)]
    for cat, af, dur, red, prio in casos:
        tid = nuevo(sol, sd, "INCIDENTE", impacto="BAJO", urgencia="BAJA")
        r = tr(ag, tid, "CLASIFICADO", categoria_incidente=cat, afectacion=af, duracion_estimada_min=dur)
        assert r.status_code == 200, (cat, r.text)
        t = r.json()
        assert (t["categoria_incidente"], t["redundancia_disponible"], t["prioridad"]) == (cat, red, prio)
    tid = nuevo(sol, sd, "INCIDENTE")
    r = tr(ag, tid, "CLASIFICADO", categoria_incidente="I1", afectacion="INTERRUPCION", duracion_estimada_min=600)
    assert r.status_code == 422 and "sugerida I3" in r.json()["detail"]
    r = tr(ag, tid, "CLASIFICADO", categoria_incidente="I0", afectacion="DEGRADACION", duracion_estimada_min=60,
           redundancia_disponible=False)
    assert r.status_code == 422
    r = tr(ag, tid, "CLASIFICADO", categoria_incidente="I3", afectacion="INTERRUPCION", duracion_estimada_min=600,
           justificacion_categoria="Afecta a toda la planta")
    assert r.status_code == 200 and r.json()["categoria_incidente"] == "I3"


# ---------------------------------------------------------------------------------------- P13
def test_p13_incidente_problema_cambio_trazable(sd):
    sol, ag = cliente("sol"), cliente("ag1")
    inc, prb, cam = nuevo(sol, sd, "INCIDENTE"), nuevo(ag, sd, "PROBLEMA"), nuevo(ag, sd, "CAMBIO")
    assert ag.post(f"/api/tickets/{prb}/relaciones", json={"destino_id": inc, "tipo": "INCIDENTE_DE_PROBLEMA"}).status_code == 422
    assert ag.post(f"/api/tickets/{inc}/relaciones", json={"destino_id": prb, "tipo": "INCIDENTE_DE_PROBLEMA"}).status_code == 201
    assert ag.post(f"/api/tickets/{cam}/relaciones", json={"destino_id": prb, "tipo": "CAMBIO_CORRIGE_PROBLEMA"}).status_code == 201
    rel = {(r["tipo"], r["origen_id"], r["destino_id"]) for r in ag.get(f"/api/tickets/{prb}").json()["relaciones"]}
    assert rel == {("INCIDENTE_DE_PROBLEMA", inc, prb), ("CAMBIO_CORRIGE_PROBLEMA", cam, prb)}
    assert any(r["destino_id"] == prb for r in sol.get(f"/api/tickets/{inc}").json()["relaciones"])


# ---------------------------------------------------------------------------------------- P14
def test_p14_recorrido_ci_servicio_y_ciclos(sd, db):
    adm, c = cliente("admin", PW_ADMIN), sd["cis"]
    db.execute(text("INSERT INTO ci_servicio VALUES (:a, :s)"), {"a": c["APP1"], "s": sd["srv"]["SE.01.02"]})
    db.commit()
    rel = lambda o, d, t: adm.post("/api/cmdb/relaciones", json={"origen_id": c[o], "destino_id": c[d], "tipo": t})  # noqa: E731
    assert rel("APP1", "BD1", "DEPENDE_DE").status_code == 201
    assert rel("BD1", "SRV1", "ALOJADO_EN").status_code == 201
    assert rel("SRV1", "APP1", "DEPENDE_DE").status_code == 201        # otro tipo de arista: aún sin ciclo DEPENDE_DE
    assert rel("BD1", "APP1", "DEPENDE_DE").status_code == 422         # cerraría ciclo APP1→BD1→APP1
    assert rel("SRV1", "NET1", "CONECTADO_A").status_code == 201
    assert rel("NET1", "SRV1", "CONECTADO_A").status_code == 201       # conectado a admite ciclos
    imp = adm.get(f"/api/cmdb/cis/{c['SRV1']}/impacto").json()
    afectados = {x["codigo"] for x in imp["cis_afectados"]}
    assert {"SRV1", "BD1", "APP1"} <= afectados and "NET1" not in afectados
    assert [s["codigo"] for s in imp["servicios_potencialmente_afectados"]] == ["SE.01.02"]
    assert "potencial" in imp["nota"]
    det = adm.get(f"/api/cmdb/cis/{c['APP1']}").json()
    assert [x["codigo"] for x in det["clasificacion"]] == ["CI", "SW", "SW-APP"]
    assert det["servicios"][0]["codigo"] == "SE.01.02"


# ---------------------------------------------------------------------------------------- P15
def test_p15_cierre_sin_resolucion_reapertura_bitacora(sd, db):
    sol, ag = cliente("sol"), cliente("ag1")
    tid = nuevo(sol, sd, "INCIDENTE")
    ag.post(f"/api/tickets/{tid}/tomar")
    for hacia, _quien, datos in RUTAS["INCIDENTE"][:3]:
        tr(ag, tid, hacia, **datos)
    assert tr(ag, tid, "CERRADO").status_code == 409                   # cierre sin resolución
    assert tr(ag, tid, "RECUPERADO").status_code == 422                # falta 'recuperacion'
    for hacia, quien, datos in RUTAS["INCIDENTE"][3:]:
        assert tr({"ag1": ag, "sol": sol}[quien], tid, hacia, **datos).status_code == 200
    assert sol.post(f"/api/tickets/{tid}/reabrir", json={}).status_code == 422          # sin motivo
    assert cliente("sol2").post(f"/api/tickets/{tid}/reabrir", json={"motivo": "x"}).status_code == 403
    r = sol.post(f"/api/tickets/{tid}/reabrir", json={"motivo": "Volvió a fallar"})
    assert r.status_code == 200 and r.json()["estado"] == "EN_ATENCION" and r.json()["reaperturas"] == 1
    reap = [b for b in ag.get(f"/api/tickets/{tid}").json()["bitacora"] if b["accion"] == "REAPERTURA"][0]
    assert reap["actor"] == "sol" and reap["motivo"] == "Volvió a fallar"
    with pytest.raises((IntegrityError, InternalError, ProgrammingError)):
        db.execute(text("DELETE FROM ticket_bitacora WHERE ticket_id=:t"), {"t": tid})
    db.rollback()


# ---------------------------------------------------------------------------------------- P16
def test_p16_filtros_y_paginacion_5000(sd, db, capsys):
    generar(db, 5, 5000)
    adm = cliente("admin", PW_ADMIN)
    r = adm.get("/api/cmdb/cis", params={"lote": "SEMILLA-5", "tamano": 50, "pagina": 3}).json()
    assert r["total"] == 5000 and r["paginas"] == 100 and len(r["items"]) == 50
    hw = db.execute(text("SELECT id FROM ci_clase WHERE codigo='HW'")).scalar()
    por_tipo = adm.get("/api/cmdb/cis", params={"clase_id": hw, "lote": "SEMILLA-5"}).json()
    esperados = db.execute(text("""SELECT count(*) FROM ci JOIN ci_clase c ON c.id=ci.clase_id
        WHERE c.padre_id=:hw AND ci.lote='SEMILLA-5'"""), {"hw": hw}).scalar()
    assert por_tipo["total"] == esperados > 0
    q = adm.get("/api/cmdb/cis", params={"q": "CI-S5-0420"}).json()
    assert q["total"] >= 1 and all("CI-S5-0420" in x["codigo"] for x in q["items"])
    with capsys.disabled():
        print(f"\n[P16] tiempos observados (ms, servidor, BD de prueba local): pagina={r['tiempo_ms']} "
              f"clase={por_tipo['tiempo_ms']} busqueda={q['tiempo_ms']}")


# ---------------------------------------------------------------------------------------- P17
def test_p17_indicadores_con_datos_controlados(sd, db):
    sol = cliente("sol")
    t0 = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)
    a, b, c = (nuevo(sol, sd, "SERVICIO_TI") for _ in range(3))
    # a: 1.ª atención 20 min, resuelto 300 min con 60 min de pausa -> 240 min (objetivos 30/480: cumple ambos)
    # b: 1.ª atención 45 min (no cumple), resuelto 600 min (no cumple)
    # c: abierto sin atención: cuenta en antigüedad, no en promedios
    for tid, pa, res in ((a, 20, 300), (b, 45, 600), (c, None, None)):
        db.execute(text("""UPDATE ticket SET creado_en=:t0, primera_atencion_en=:pa, resuelto_en=:re,
            estado=CASE WHEN :re2 THEN 'CUMPLIDO' ELSE estado END WHERE id=:id"""),
                   {"t0": t0, "pa": t0 + timedelta(minutes=pa) if pa else None,
                    "re": t0 + timedelta(minutes=res) if res else None, "re2": res is not None, "id": tid})
    db.execute(text("INSERT INTO ticket_pausa (ticket_id, inicio, fin) VALUES (:t, :i, :f)"),
               {"t": a, "i": t0 + timedelta(minutes=100), "f": t0 + timedelta(minutes=160)})
    db.commit()
    k = indicadores.calcular(db, ahora=t0 + timedelta(minutes=1000))
    assert k["primera_atencion_min"] == {"n": 2, "promedio": 32.5, "maximo": 45.0}
    assert k["resolucion_min"] == {"n": 2, "promedio": 420.0, "maximo": 600.0}
    assert k["tickets_sin_resolver_excluidos"] == 1 and k["abiertos"] == 3
    assert k["antiguedad_abiertos_min"]["maximo"] == 1000.0
    assert k["cumplimiento_primera_atencion"] == {"medibles": 2, "cumplen": 1, "porcentaje": 50.0}
    assert k["cumplimiento_resolucion"] == {"medibles": 2, "cumplen": 1, "porcentaje": 50.0}
    assert k["base_tiempo"] == "calendario" and k["por_proceso"] == {"SERVICIO_TI": 3}


# ---------------------------------------------------------------------------------------- seguridad adicional
def test_adjuntos_comentarios_internos_y_autorizacion(sd):
    sol, ag = cliente("sol"), cliente("ag1")
    tid = nuevo(sol, sd, "SERVICIO_TI")
    ok = sol.post(f"/api/tickets/{tid}/adjuntos", files={"archivo": ("e.txt", io.BytesIO(b"hola"), "text/plain")})
    assert ok.status_code == 201
    assert sol.post(f"/api/tickets/{tid}/adjuntos", files={"archivo": ("x.exe", io.BytesIO(b"MZ"), "application/x-msdownload")}).status_code == 415
    grande = io.BytesIO(b"a" * (2 * 1024 * 1024 + 1))
    assert sol.post(f"/api/tickets/{tid}/adjuntos", files={"archivo": ("g.txt", grande, "text/plain")}).status_code == 413
    assert sol.get(f"/api/tickets/{tid}/adjuntos/{ok.json()['id']}").content == b"hola"
    assert cliente("sol2").get(f"/api/tickets/{tid}/adjuntos/{ok.json()['id']}").status_code == 403
    assert sol.post(f"/api/tickets/{tid}/comentarios", json={"texto": "x", "interno": True}).status_code == 403
    ag.post(f"/api/tickets/{tid}/comentarios", json={"texto": "nota interna", "interno": True})
    ag.post(f"/api/tickets/{tid}/comentarios", json={"texto": "respuesta visible"})
    vistos = [c["texto"] for c in sol.get(f"/api/tickets/{tid}").json()["comentarios"]]
    assert vistos == ["respuesta visible"]
    assert sol.get(f"/api/tickets/{tid}").json()["bitacora"] == []


def test_sin_huerfanos_al_desactivar_usuario_y_cola(sd, db):
    sol, ag, adm = cliente("sol"), cliente("ag1"), cliente("admin", PW_ADMIN)
    tid = nuevo(sol, sd, "SERVICIO_TI")
    ag.post(f"/api/tickets/{tid}/tomar")
    assert adm.post(f"/api/usuarios/{sd['u']['ag1']}/desactivar").status_code == 200
    assert db.execute(text("SELECT responsable_id FROM ticket WHERE id=:t"), {"t": tid}).scalar() is None
    r = adm.post(f"/api/colas/{sd['colas']['SRV']}/desactivar")
    assert r.status_code == 409 and r.json()["tickets_abiertos"] == 1
    otra = adm.post("/api/colas", json={"codigo": "SRV2", "nombre": "SRV2", "proceso_codigo": "SERVICIO_TI",
                                        "nivel_soporte": 1}).json()
    assert adm.post(f"/api/colas/{sd['colas']['SRV']}/desactivar", params={"cola_destino": otra["id"]}).json()["tickets_movidos"] == 1
    assert db.execute(text("SELECT cola_id FROM ticket WHERE id=:t"), {"t": tid}).scalar() == otra["id"]
    assert tk.FLUJOS  # referencia para el linter
